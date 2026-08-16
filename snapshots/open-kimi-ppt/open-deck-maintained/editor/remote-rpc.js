export const DEFAULT_REMOTE_RPC_TIMEOUT_MS = 15_000;

const SESSION_UNUSABLE_CODES = new Set([
  "CONNECTION_DESTROYED",
  "METHOD_CALL_TIMEOUT",
  "REMOTE_METHOD_UNAVAILABLE",
  "REMOTE_RPC_SESSION_EXPIRED",
  "TRANSMISSION_FAILED",
]);

function assertTimeout(timeoutMs) {
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    throw new TypeError("RPC timeout must be a positive safe integer");
  }
}

function createRpcError(
  method,
  cause,
  {
    code = cause?.code || "REMOTE_RPC_FAILED",
    mayHaveApplied = true,
  } = {},
) {
  const detail = cause?.message || String(cause);
  const error = new Error(`远端 ${method} 调用失败：${detail}`, { cause });
  error.name = "RemoteRpcError";
  error.code = code;
  error.rpcMethod = method;
  error.remoteMayHaveApplied = Boolean(mayHaveApplied);
  error.remoteSessionUnusable = SESSION_UNUSABLE_CODES.has(code);
  return error;
}

function timeoutError(method, timeoutMs, mayHaveApplied) {
  const cause = new Error(`${method} timed out after ${timeoutMs}ms`);
  cause.code = "METHOD_CALL_TIMEOUT";
  return createRpcError(method, cause, {
    code: cause.code,
    mayHaveApplied,
  });
}

/**
 * Invoke one post-handshake remote method with a bounded lifetime.
 *
 * In production, `CallOptionsClass` is Penpal's branded CallOptions class. Its
 * native timeout removes the pending reply handler, so late replies are
 * ignored at the transport boundary. The outer timer is still required for
 * compatible test doubles and non-Penpal adapters; their late settlement is
 * observed and consumed, but can no longer settle this returned promise.
 */
export function callRemote(
  remote,
  method,
  args = [],
  {
    timeoutMs = DEFAULT_REMOTE_RPC_TIMEOUT_MS,
    mayHaveApplied = true,
    CallOptionsClass = null,
    isSessionCurrent = () => true,
  } = {},
) {
  assertTimeout(timeoutMs);
  if (!remote || typeof remote[method] !== "function") {
    return Promise.reject(createRpcError(
      method,
      new Error(`远端方法 ${method} 不可用`),
      { code: "REMOTE_METHOD_UNAVAILABLE", mayHaveApplied: false },
    ));
  }
  if (!isSessionCurrent()) {
    return Promise.reject(createRpcError(
      method,
      new Error("远端会话已经过期"),
      { code: "REMOTE_RPC_SESSION_EXPIRED", mayHaveApplied: false },
    ));
  }

  let result;
  try {
    const callArgs = [...args];
    if (CallOptionsClass) {
      callArgs.push(new CallOptionsClass({ timeout: timeoutMs }));
    }
    result = remote[method](...callArgs);
  } catch (cause) {
    return Promise.reject(createRpcError(method, cause, {
      mayHaveApplied: false,
    }));
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback, value) => {
      if (settled) return false;
      settled = true;
      clearTimeout(timer);
      callback(value);
      return true;
    };
    const timer = setTimeout(() => {
      finish(reject, timeoutError(method, timeoutMs, mayHaveApplied));
    }, timeoutMs);

    Promise.resolve(result).then(
      (value) => {
        if (settled) return;
        if (!isSessionCurrent()) {
          finish(reject, createRpcError(
            method,
            new Error("远端调用属于已经过期的会话"),
            { code: "REMOTE_RPC_SESSION_EXPIRED", mayHaveApplied },
          ));
          return;
        }
        finish(resolve, value);
      },
      (cause) => {
        if (settled) return;
        finish(reject, createRpcError(method, cause, { mayHaveApplied }));
      },
    );
  });
}

export function createRemoteRpc({
  CallOptionsClass = null,
  defaultTimeoutMs = DEFAULT_REMOTE_RPC_TIMEOUT_MS,
} = {}) {
  assertTimeout(defaultTimeoutMs);
  return (remote, method, args = [], options = {}) => callRemote(
    remote,
    method,
    args,
    {
      timeoutMs: defaultTimeoutMs,
      CallOptionsClass,
      ...options,
    },
  );
}

export function isRemoteRpcTimeout(error) {
  return error?.code === "METHOD_CALL_TIMEOUT";
}
