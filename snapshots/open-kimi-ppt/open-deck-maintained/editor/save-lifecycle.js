import { recordSaveFailure, resolveSaveFailure } from "./lib.js";
import {
  callRemote,
  DEFAULT_REMOTE_RPC_TIMEOUT_MS,
} from "./remote-rpc.js";

export const MAX_PENDING_SAVES = 8;
export const MAX_PENDING_SAVE_BYTES = 100 * 1024 * 1024;

function clearSaveLifecycle(state) {
  state.saveFailure = null;
  state.saveBlocked = false;
  state.switchPoison = null;
  state.remoteUncertainty = null;
}

export function requiresDiscardReload(state) {
  return (
    state.saveBlocked
    || state.saveFailure?.revision === state.revision
  );
}

export function hasUncertainSaveState(state) {
  return (
    state.saveBlocked
    || Boolean(state.remoteUncertainty)
    || Boolean(
      state.saveFailure?.revision === state.revision
      && state.saveFailure.unscoped
    )
  );
}

/**
 * Clear ordinary save state after a proven project commit.
 *
 * Any unresolved failure is deliberately excluded: scoped failures may be
 * cleared only by retrying every failed path or by an explicitly confirmed
 * discard-and-reload transaction. Unscoped/uncertain failures have no
 * path-level retry and therefore require the latter.
 */
export function resetSaveLifecycle(state) {
  if (requiresDiscardReload(state) || state.discardReloadAuthorization) {
    throw new Error("保存状态无法自动确认；必须由用户确认丢弃更改并重新载入文稿");
  }
  clearSaveLifecycle(state);
}

/**
 * Authorize exactly one discard-and-reload transaction without clearing the
 * evidence yet. Only a successful deck commit may consume this token.
 */
export function authorizeDiscardReload(state, { confirmed = false } = {}) {
  if (confirmed !== true) return null;
  if (!requiresDiscardReload(state)) return null;
  if (state.preparingSwitch || state.switching || state.switchLease) {
    throw new Error("文稿切换仍在收尾，请稍后再确认重新载入");
  }
  if (state.discardReloadAuthorization) {
    throw new Error("已有一次确认丢弃并重新载入操作正在进行");
  }
  const authorization = Object.freeze({
    revision: state.revision,
    saveFailure: state.saveFailure,
    saveBlocked: state.saveBlocked,
    switchPoison: state.switchPoison,
    remoteUncertainty: state.remoteUncertainty,
  });
  state.discardReloadAuthorization = authorization;
  return authorization;
}

export function abortDiscardReload(state, authorization) {
  if (!authorization || state.discardReloadAuthorization !== authorization) return false;
  const currentFailure = state.saveFailure?.revision === state.revision
    ? state.saveFailure
    : null;
  const authorizedFailure = authorization.saveFailure?.revision === state.revision
    ? authorization.saveFailure
    : null;
  if (currentFailure && authorizedFailure && currentFailure !== authorizedFailure) {
    state.saveFailure = recordSaveFailure(
      currentFailure,
      state.revision,
      currentFailure.error,
      authorizedFailure.paths ?? new Set(),
      {
        unscoped: Boolean(authorizedFailure.unscoped),
        pathAliases: authorizedFailure.pathAliases,
      },
    );
  } else {
    state.saveFailure = currentFailure ?? authorizedFailure;
  }
  // Aborting may follow a failed freeze/thaw or a late-save poison. Those are
  // stronger, newer safety facts than the snapshot captured at authorization
  // time and must never be cleared by rollback of the user confirmation.
  state.switchPoison = state.switchPoison ?? authorization.switchPoison;
  state.remoteUncertainty = state.remoteUncertainty ?? authorization.remoteUncertainty;
  state.saveBlocked = Boolean(
    state.saveBlocked
    || authorization.saveBlocked
    || state.saveFailure?.unscoped
    || state.remoteUncertainty
  );
  state.discardReloadAuthorization = null;
  return true;
}

export function assertDiscardReloadAuthorized(state, authorization) {
  if (!authorization || state.discardReloadAuthorization !== authorization) {
    throw new Error("确认丢弃并重新载入的授权已经过期");
  }
  if (authorization.revision !== state.revision) {
    throw new Error("文稿版本已变化，不能使用旧的丢弃授权");
  }
  return authorization;
}

export function commitDiscardReload(state, authorization) {
  assertDiscardReloadAuthorized(state, authorization);
  clearSaveLifecycle(state);
  state.discardReloadAuthorization = null;
  return true;
}

function switchError(message, code, cause) {
  const error = new Error(message, cause ? { cause } : undefined);
  error.code = code;
  return error;
}

function nextSwitchLease(
  state,
  phase = "switch",
  discardAuthorization = null,
  {
    invokeRemote = callRemote,
    rpcTimeoutMs = DEFAULT_REMOTE_RPC_TIMEOUT_MS,
    onRemoteUncertain = () => undefined,
  } = {},
) {
  const epoch = (Number.isSafeInteger(state.switchEpoch) ? state.switchEpoch : 0) + 1;
  state.switchEpoch = epoch;
  return Object.freeze({
    epoch,
    phase,
    freeze: { promise: null },
    discardAuthorization,
    invokeRemote,
    rpcTimeoutMs,
    onRemoteUncertain,
  });
}

export function poisonRemoteSession(state, error, lease = null) {
  if (state.remoteUncertainty?.error === error) {
    state.saveBlocked = true;
    return state.remoteUncertainty;
  }
  const marker = Object.freeze({
    error,
    lease,
    previous: state.remoteUncertainty ?? null,
    previouslyBlocked: Boolean(state.saveBlocked),
  });
  state.remoteUncertainty = marker;
  state.saveBlocked = true;
  return marker;
}

function notifyRemoteUncertain(callback, error, lease) {
  try {
    callback(error, lease);
  } catch {
    // The fail-closed state was recorded before notification. A host cleanup
    // failure must not replace the RPC error or reopen the save gate.
  }
}

function ownsSwitchLease(state, lease) {
  return Boolean(lease) && state.switchLease === lease;
}

function assertSwitchLeaseOwner(state, lease) {
  if (!ownsSwitchLease(state, lease)) {
    throw switchError("文稿切换凭据已经过期", "SWITCH_LEASE_EXPIRED");
  }
}

function ownsDiscardAuthorization(state, authorization) {
  return Boolean(authorization) && state.discardReloadAuthorization === authorization;
}

export function isSaveQueueSwitchPoisoned(state, lease = state.switchLease) {
  return Boolean(lease && state.switchPoison?.lease === lease);
}

export function assertSaveQueueSwitchClean(
  state,
  lease,
  { requireClosedGate = true } = {},
) {
  assertSwitchLeaseOwner(state, lease);
  if (isSaveQueueSwitchPoisoned(state, lease)) {
    throw state.switchPoison.error;
  }
  if (state.saveBlocked && !ownsDiscardAuthorization(state, lease.discardAuthorization)) {
    throw switchError(
      "编辑器状态尚未确认；必须确认丢弃更改并重新载入文稿",
      "SAVE_STATE_BLOCKED",
    );
  }
  if (requireClosedGate && !state.switching) {
    throw switchError("文稿切换的保存闸门尚未关闭", "SWITCH_GATE_OPEN");
  }
  return lease;
}

function poisonActiveSwitch(state) {
  const lease = state.switchLease;
  if (!state.switching || !ownsSwitchLease(state, lease) || lease.phase !== "switch") {
    return null;
  }
  if (isSaveQueueSwitchPoisoned(state, lease)) return state.switchPoison;
  const error = switchError(
    "文稿切换期间收到迟到的保存请求；切换已冻结，请确认丢弃更改并重新载入",
    "SWITCH_POISONED_BY_LATE_SAVE",
  );
  const poison = Object.freeze({ lease, error });
  state.switchPoison = poison;
  state.saveBlocked = true;
  return poison;
}

export async function freezeAndDrainSaveQueue(
  state,
  remote,
  {
    discardAuthorization = null,
    invokeRemote = callRemote,
    rpcTimeoutMs = DEFAULT_REMOTE_RPC_TIMEOUT_MS,
    onRemoteUncertain = () => undefined,
  } = {},
) {
  if (state.preparingSwitch || state.switching || state.switchLease) {
    throw new Error("另一个文稿切换操作仍在进行");
  }
  if (discardAuthorization && !ownsDiscardAuthorization(state, discardAuthorization)) {
    throw new Error("确认丢弃并重新载入的授权已经过期");
  }
  if (state.saveBlocked && !discardAuthorization) {
    throw new Error("编辑器状态尚未确认；请确认丢弃更改并重新载入当前文稿");
  }

  const lease = nextSwitchLease(
    state,
    "switch",
    discardAuthorization,
    { invokeRemote, rpcTimeoutMs, onRemoteUncertain },
  );
  state.switchLease = lease;
  if (!discardAuthorization) state.switchPoison = null;
  state.preparingSwitch = true;
  state.switching = false;
  let freezeConfirmed = false;
  try {
    // Keep accepting saves until the remote confirms that editing is frozen.
    // Once confirmed, close the save gate and drain everything accepted before it.
    const freezeRequest = invokeRemote(
      remote,
      "setEditable",
      [false],
      { timeoutMs: rpcTimeoutMs, mayHaveApplied: true },
    );
    lease.freeze.promise = freezeRequest;
    await freezeRequest;
    freezeConfirmed = true;
    assertSwitchLeaseOwner(state, lease);
    state.switching = true;
    assertSaveQueueSwitchClean(state, lease);
    await state.saveQueue;
    assertSaveQueueSwitchClean(state, lease);
    if (
      state.saveFailure?.revision === state.revision
      && !ownsDiscardAuthorization(state, lease.discardAuthorization)
    ) {
      throw new Error(`最近一次保存失败，已取消切换：${state.saveFailure.error.message}`);
    }
    return lease;
  } catch (error) {
    // A cancellation owns a newer lease. An expired continuation must never
    // reopen or close gates belonging to that newer operation.
    if (!ownsSwitchLease(state, lease)) throw error;

    state.switching = false;
    if (error?.remoteMayHaveApplied) {
      // Never issue a compensating call on this session: the timed-out remote
      // mutation may still apply after the compensation. Only a fresh iframe
      // session may later clear this poison through a confirmed discard reload.
      poisonRemoteSession(state, error, lease);
      notifyRemoteUncertain(onRemoteUncertain, error, lease);
    } else if (freezeConfirmed && !state.saveBlocked) {
      // The freeze was confirmed and a later local drain/check failed. A
      // bounded thaw on the still-certain session is safe to attempt.
      try {
        await invokeRemote(
          remote,
          "setEditable",
          [!state.readOnlyFallback],
          { timeoutMs: rpcTimeoutMs, mayHaveApplied: true },
        );
      } catch (restoreError) {
        if (ownsSwitchLease(state, lease)) {
          poisonRemoteSession(state, restoreError, lease);
          notifyRemoteUncertain(onRemoteUncertain, restoreError, lease);
        }
      }
    }
    if (ownsSwitchLease(state, lease)) {
      state.switchLease = null;
      state.preparingSwitch = false;
    }
    throw error;
  }
}

/**
 * Open the local save gate before the iframe can become editable again.
 *
 * `preparingSwitch` deliberately remains set: it is the operation mutex and
 * prevents another deck switch from starting while the caller finishes its
 * post-commit work. `switching` is only the save gate once the remote has been
 * frozen.
 */
export async function restoreEditingAfterSaveDrain(
  state,
  remote,
  editable,
  lease,
  {
    invokeRemote = lease?.invokeRemote ?? callRemote,
    rpcTimeoutMs = lease?.rpcTimeoutMs ?? DEFAULT_REMOTE_RPC_TIMEOUT_MS,
    onRemoteUncertain = lease?.onRemoteUncertain ?? (() => undefined),
  } = {},
) {
  assertSaveQueueSwitchClean(state, lease);
  state.switching = false;
  try {
    await invokeRemote(
      remote,
      "setEditable",
      [editable],
      { timeoutMs: rpcTimeoutMs, mayHaveApplied: true },
    );
  } catch (error) {
    if (ownsSwitchLease(state, lease)) {
      poisonRemoteSession(state, error, lease);
      notifyRemoteUncertain(onRemoteUncertain, error, lease);
    }
    throw error;
  }
  assertSwitchLeaseOwner(state, lease);
}

export function finishSaveQueueSwitch(state, lease) {
  if (!ownsSwitchLease(state, lease)) return false;
  state.switching = false;
  state.preparingSwitch = false;
  state.switchLease = null;
  return true;
}

export async function cancelSaveQueueSwitch(
  state,
  remote,
  {
    lease = state.switchLease,
    onRestoreError = () => undefined,
    invokeRemote = lease?.invokeRemote ?? callRemote,
    rpcTimeoutMs = lease?.rpcTimeoutMs ?? DEFAULT_REMOTE_RPC_TIMEOUT_MS,
    onRemoteUncertain = lease?.onRemoteUncertain ?? (() => undefined),
  } = {},
) {
  if (!lease || !ownsSwitchLease(state, lease)) return false;

  // Invalidate the original owner before any await. The cancellation lease
  // keeps the operation mutex until the remote thaw has settled.
  const cancellationLease = nextSwitchLease(
    state,
    "cancel",
    null,
    { invokeRemote, rpcTimeoutMs, onRemoteUncertain },
  );
  state.switchLease = cancellationLease;
  state.switching = false;
  state.preparingSwitch = true;
  try {
    // Wait for the superseded freeze request before sending the final thaw.
    // Otherwise an out-of-order false reply could freeze the remote again
    // after cancellation has apparently completed.
    let supersededFreezeError = null;
    await lease.freeze?.promise?.catch((error) => {
      supersededFreezeError = error;
    });
    if (!ownsSwitchLease(state, cancellationLease)) return false;
    if (supersededFreezeError?.remoteMayHaveApplied) {
      poisonRemoteSession(state, supersededFreezeError, cancellationLease);
      notifyRemoteUncertain(
        onRemoteUncertain,
        supersededFreezeError,
        cancellationLease,
      );
    }
    if (remote && !state.saveBlocked) {
      try {
        await invokeRemote(
          remote,
          "setEditable",
          [!state.readOnlyFallback],
          { timeoutMs: rpcTimeoutMs, mayHaveApplied: true },
        );
      } catch (error) {
        if (ownsSwitchLease(state, cancellationLease)) {
          poisonRemoteSession(state, error, cancellationLease);
          notifyRemoteUncertain(onRemoteUncertain, error, cancellationLease);
        }
        onRestoreError(error);
      }
    }
  } finally {
    if (ownsSwitchLease(state, cancellationLease)) {
      state.switchLease = null;
      state.preparingSwitch = false;
    }
  }
  return true;
}

export function enqueueSave(
  state,
  payload,
  {
    captureContext,
    getAttempt,
    isActiveContext,
    persistChanges,
    onStickyFailure = () => undefined,
    onRejected = () => undefined,
    onSwitchPoison = () => undefined,
    maxPendingSaves = MAX_PENDING_SAVES,
    getPayloadBytes = () => 0,
    sanitizePayload = (value) => value,
    maxPendingSaveBytes = MAX_PENDING_SAVE_BYTES,
  },
) {
  if (state.switching) {
    const poison = poisonActiveSwitch(state);
    const error = poison?.error
      ?? new Error("文稿正在切换，已拒绝迟到的保存请求");
    if (poison) onSwitchPoison(error, poison);
    return Promise.reject(error);
  }
  if (state.saveBlocked) {
    return Promise.reject(new Error("编辑器状态尚未确认，已阻止保存；请重新载入文稿"));
  }
  const rejectBeforeQueue = (error) => {
    error.saveScopeUncertain = true;
    state.saveFailure = recordSaveFailure(
      state.saveFailure,
      state.revision,
      error,
      new Set(),
      { unscoped: true },
    );
    state.saveBlocked = true;
    const context = Object.freeze({ revision: state.revision });
    onRejected(error, context);
    return Promise.reject(error);
  };
  if (state.pendingSaveCount >= maxPendingSaves) {
    const error = new Error(
      `保存队列已达到 ${maxPendingSaves} 项安全上限；已冻结后续保存，请重新载入文稿`,
    );
    error.code = "SAVE_QUEUE_OVERFLOW";
    return rejectBeforeQueue(error);
  }
  let queuedPayload;
  let payloadBytes;
  try {
    queuedPayload = sanitizePayload(payload);
    payloadBytes = getPayloadBytes(queuedPayload);
  } catch (cause) {
    const error = cause instanceof Error ? cause : new Error(String(cause));
    error.code ||= "SAVE_PAYLOAD_OVERFLOW";
    return rejectBeforeQueue(error);
  }
  const pendingSaveBytes = Number.isSafeInteger(state.pendingSaveBytes)
    ? state.pendingSaveBytes
    : 0;
  if (
    !Number.isSafeInteger(payloadBytes)
    || payloadBytes < 0
    || payloadBytes > maxPendingSaveBytes - pendingSaveBytes
  ) {
    const error = new Error(
      `保存队列文本总量超过 ${Math.floor(maxPendingSaveBytes / 1024 / 1024)} MiB 安全上限`,
    );
    error.code = "SAVE_QUEUE_BYTES_OVERFLOW";
    return rejectBeforeQueue(error);
  }

  const context = captureContext();
  const attempt = getAttempt(queuedPayload, context);
  const attemptPaths = attempt.paths;
  state.pendingSaveCount += 1;
  state.pendingSaveBytes = pendingSaveBytes + payloadBytes;
  const operation = state.saveQueue.then(() => {
    // A later save may have entered the queue before an earlier task proved
    // disk/editor state uncertain. Re-check at execution time so already
    // queued work cannot mutate files after the fail-closed latch is raised.
    if (state.saveBlocked) {
      const error = new Error("先前保存的磁盘结果无法确认，已取消排队中的后续保存；请重新载入文稿");
      error.saveScopeUncertain = true;
      throw error;
    }
    if (!isActiveContext(context)) {
      throw new Error("排队保存所属的文稿会话已经过期");
    }
    return persistChanges(queuedPayload, context);
  });
  const tracked = operation.then(
    (result) => {
      if (isActiveContext(context) && state.saveFailure?.revision === context.revision) {
        state.saveFailure = resolveSaveFailure(
          state.saveFailure,
          context.revision,
          attemptPaths,
          { pathAliases: attempt.pathAliases },
        );
        if (state.saveFailure) onStickyFailure(state.saveFailure, context);
      }
      return result;
    },
    (error) => {
      if (isActiveContext(context)) {
        state.saveFailure = recordSaveFailure(
          state.saveFailure,
          context.revision,
          error,
          attemptPaths,
          {
            unscoped: attempt.unscoped || Boolean(error?.saveScopeUncertain),
            pathAliases: attempt.pathAliases,
          },
        );
        if (state.saveFailure.unscoped) state.saveBlocked = true;
      }
      throw error;
    },
  ).finally(() => {
    state.pendingSaveCount = Math.max(0, state.pendingSaveCount - 1);
    state.pendingSaveBytes = Math.max(0, state.pendingSaveBytes - payloadBytes);
  });
  state.saveQueue = tracked.catch(() => undefined);
  return tracked.catch((error) => {
    onRejected(error, context);
    throw error;
  });
}

export function shouldWarnBeforeUnload(state, saveStateKind) {
  return (
    state.pendingSaveCount > 0
    || state.saveBlocked
    || state.saveFailure?.revision === state.revision
    || saveStateKind === "saving"
  );
}
