// Vendored from penpal@7.0.6 (MIT); see penpal.LICENSE.txt.
// src/PenpalError.ts
var PenpalError = class extends Error {
  code;
  constructor(code, message) {
    super(message);
    this.name = "PenpalError";
    this.code = code;
  }
};
var PenpalError_default = PenpalError;

// src/errorSerialization.ts
var serializeError = (error) => ({
  name: error.name,
  message: error.message,
  stack: error.stack,
  penpalCode: error instanceof PenpalError_default ? error.code : void 0
});
var deserializeError = ({
  name,
  message,
  stack,
  penpalCode
}) => {
  const deserializedError = penpalCode ? new PenpalError_default(penpalCode, message) : new Error(message);
  deserializedError.name = name;
  deserializedError.stack = stack;
  return deserializedError;
};

// src/Reply.ts
var brand = Symbol("Reply");
var Reply = class {
  value;
  transferables;
  // Allows TypeScript to distinguish between an actual instance of this
  // class versus an object that looks structurally similar.
  // eslint-disable-next-line no-unused-private-class-members
  #brand = brand;
  constructor(value, options) {
    this.value = value;
    this.transferables = options?.transferables;
  }
};
var Reply_default = Reply;

// src/namespace.ts
var namespace_default = "penpal";

// src/guards.ts
var isObject = (value) => {
  return typeof value === "object" && value !== null;
};
var isFunction = (value) => {
  return typeof value === "function";
};
var isMessage = (data) => {
  return isObject(data) && data.namespace === namespace_default;
};
var isSynMessage = (message) => {
  return message.type === "SYN";
};
var isAck1Message = (message) => {
  return message.type === "ACK1";
};
var isAck2Message = (message) => {
  return message.type === "ACK2";
};
var isCallMessage = (message) => {
  return message.type === "CALL";
};
var isReplyMessage = (message) => {
  return message.type === "REPLY";
};
var isDestroyMessage = (message) => {
  return message.type === "DESTROY";
};

// src/methodSerialization.ts
var extractMethodPathsFromMethods = (methods, currentPath = []) => {
  const methodPaths = [];
  for (const key of Object.keys(methods)) {
    const value = methods[key];
    if (isFunction(value)) {
      methodPaths.push([...currentPath, key]);
    } else if (isObject(value)) {
      methodPaths.push(
        ...extractMethodPathsFromMethods(value, [...currentPath, key])
      );
    }
  }
  return methodPaths;
};
var getMethodAtMethodPath = (methodPath, methods) => {
  const result = methodPath.reduce(
    (acc, pathSegment) => {
      return isObject(acc) ? acc[pathSegment] : void 0;
    },
    methods
  );
  return isFunction(result) ? result : void 0;
};
var formatMethodPath = (methodPath) => {
  return methodPath.join(".");
};

// src/connectCallHandler.ts
var createErrorReplyMessage = (channel, callId, error) => ({
  namespace: namespace_default,
  channel,
  type: "REPLY",
  callId,
  isError: true,
  ...error instanceof Error ? { value: serializeError(error), isSerializedErrorInstance: true } : { value: error }
});
var connectCallHandler = (messenger, methods, channel, log) => {
  let isDestroyed = false;
  const handleMessage = async (message) => {
    if (isDestroyed) {
      return;
    }
    if (!isCallMessage(message)) {
      return;
    }
    log?.(`Received ${formatMethodPath(message.methodPath)}() call`, message);
    const { methodPath, args, id: callId } = message;
    let replyMessage;
    let transferables;
    try {
      const method = getMethodAtMethodPath(methodPath, methods);
      if (!method) {
        throw new PenpalError_default(
          "METHOD_NOT_FOUND",
          `Method \`${formatMethodPath(methodPath)}\` is not found.`
        );
      }
      let value = await method(...args);
      if (value instanceof Reply_default) {
        transferables = value.transferables;
        value = await value.value;
      }
      replyMessage = {
        namespace: namespace_default,
        channel,
        type: "REPLY",
        callId,
        value
      };
    } catch (error) {
      replyMessage = createErrorReplyMessage(channel, callId, error);
    }
    if (isDestroyed) {
      return;
    }
    try {
      log?.(`Sending ${formatMethodPath(methodPath)}() reply`, replyMessage);
      messenger.sendMessage(replyMessage, transferables);
    } catch (error) {
      if (error.name === "DataCloneError") {
        replyMessage = createErrorReplyMessage(channel, callId, error);
        log?.(`Sending ${formatMethodPath(methodPath)}() reply`, replyMessage);
        messenger.sendMessage(replyMessage);
      }
      throw error;
    }
  };
  messenger.addMessageHandler(handleMessage);
  return () => {
    isDestroyed = true;
    messenger.removeMessageHandler(handleMessage);
  };
};
var connectCallHandler_default = connectCallHandler;

// src/generateId.ts
var generateId_default = crypto.randomUUID?.bind(crypto) ?? (() => new Array(4).fill(0).map(
  () => Math.floor(Math.random() * Number.MAX_SAFE_INTEGER).toString(16)
).join("-"));

// src/CallOptions.ts
var brand2 = Symbol("CallOptions");
var CallOptions = class {
  transferables;
  timeout;
  // Allows TypeScript to distinguish between an actual instance of this
  // class versus an object that looks structurally similar.
  // eslint-disable-next-line no-unused-private-class-members
  #brand = brand2;
  constructor(options) {
    this.transferables = options?.transferables;
    this.timeout = options?.timeout;
  }
};
var CallOptions_default = CallOptions;

// src/connectRemoteProxy.ts
var methodsToTreatAsNative = /* @__PURE__ */ new Set(["apply", "call", "bind"]);
var createRemoteProxy = (callback, log, path = []) => {
  return new Proxy(
    path.length ? () => {
    } : /* @__PURE__ */ Object.create(null),
    {
      get(target, prop) {
        if (prop === "then") {
          return;
        }
        if (path.length && methodsToTreatAsNative.has(prop)) {
          return Reflect.get(target, prop);
        }
        return createRemoteProxy(callback, log, [...path, prop]);
      },
      apply(target, _thisArg, args) {
        return callback(path, args);
      }
    }
  );
};
var getDestroyedConnectionMethodCallError = (methodPath) => {
  return new PenpalError_default(
    "CONNECTION_DESTROYED",
    `Method call ${formatMethodPath(
      methodPath
    )}() failed due to destroyed connection`
  );
};
var connectRemoteProxy = (messenger, channel, log) => {
  let isDestroyed = false;
  const replyHandlers = /* @__PURE__ */ new Map();
  const handleMessage = (message) => {
    if (!isReplyMessage(message)) {
      return;
    }
    const { callId, value, isError, isSerializedErrorInstance } = message;
    const replyHandler = replyHandlers.get(callId);
    if (!replyHandler) {
      return;
    }
    replyHandlers.delete(callId);
    log?.(
      `Received ${formatMethodPath(replyHandler.methodPath)}() call`,
      message
    );
    if (isError) {
      replyHandler.reject(
        isSerializedErrorInstance ? deserializeError(value) : value
      );
    } else {
      replyHandler.resolve(value);
    }
  };
  messenger.addMessageHandler(handleMessage);
  const remoteProxy = createRemoteProxy((methodPath, args) => {
    if (isDestroyed) {
      throw getDestroyedConnectionMethodCallError(methodPath);
    }
    const callId = generateId_default();
    const lastArg = args[args.length - 1];
    const lastArgIsOptions = lastArg instanceof CallOptions_default;
    const { timeout, transferables } = lastArgIsOptions ? lastArg : {};
    const argsWithoutOptions = lastArgIsOptions ? args.slice(0, -1) : args;
    return new Promise((resolve, reject) => {
      const timeoutId = timeout !== void 0 ? window.setTimeout(() => {
        replyHandlers.delete(callId);
        reject(
          new PenpalError_default(
            "METHOD_CALL_TIMEOUT",
            `Method call ${formatMethodPath(
              methodPath
            )}() timed out after ${timeout}ms`
          )
        );
      }, timeout) : void 0;
      replyHandlers.set(callId, { methodPath, resolve, reject, timeoutId });
      try {
        const callMessage = {
          namespace: namespace_default,
          channel,
          type: "CALL",
          id: callId,
          methodPath,
          args: argsWithoutOptions
        };
        log?.(`Sending ${formatMethodPath(methodPath)}() call`, callMessage);
        messenger.sendMessage(callMessage, transferables);
      } catch (error) {
        reject(
          new PenpalError_default("TRANSMISSION_FAILED", error.message)
        );
      }
    });
  }, log);
  const destroy = () => {
    isDestroyed = true;
    messenger.removeMessageHandler(handleMessage);
    for (const { methodPath, reject, timeoutId } of replyHandlers.values()) {
      clearTimeout(timeoutId);
      reject(getDestroyedConnectionMethodCallError(methodPath));
    }
    replyHandlers.clear();
  };
  return {
    remoteProxy,
    destroy
  };
};
var connectRemoteProxy_default = connectRemoteProxy;

// src/getPromiseWithResolvers.ts
var getPromiseWithResolvers = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return {
    promise,
    resolve,
    reject
  };
};
var getPromiseWithResolvers_default = getPromiseWithResolvers;

// src/backwardCompatibility.ts
var DEPRECATED_PENPAL_PARTICIPANT_ID = "deprecated-penpal";
var isDeprecatedMessage = (data) => {
  return isObject(data) && "penpal" in data;
};
var upgradeMethodPath = (methodPath) => methodPath.split(".");
var downgradeMethodPath = (methodPath) => methodPath.join(".");
var stringifyUnknownMessage = (message) => {
  try {
    return JSON.stringify(message);
  } catch (_) {
    return String(message);
  }
};
var getUnexpectedMessageError = (message) => {
  return new PenpalError_default(
    "TRANSMISSION_FAILED",
    `Unexpected message to translate: ${stringifyUnknownMessage(message)}`
  );
};
var upgradeMessage = (message) => {
  if (message.penpal === "syn" /* Syn */) {
    return {
      namespace: namespace_default,
      channel: void 0,
      type: "SYN",
      participantId: DEPRECATED_PENPAL_PARTICIPANT_ID
    };
  }
  if (message.penpal === "ack" /* Ack */) {
    return {
      namespace: namespace_default,
      channel: void 0,
      type: "ACK2"
    };
  }
  if (message.penpal === "call" /* Call */) {
    return {
      namespace: namespace_default,
      channel: void 0,
      type: "CALL",
      // Actually converting the ID to a string would break communication.
      id: message.id,
      methodPath: upgradeMethodPath(message.methodName),
      args: message.args
    };
  }
  if (message.penpal === "reply" /* Reply */) {
    if (message.resolution === "fulfilled" /* Fulfilled */) {
      return {
        namespace: namespace_default,
        channel: void 0,
        type: "REPLY",
        // Actually converting the ID to a string would break communication.
        callId: message.id,
        value: message.returnValue
      };
    } else {
      return {
        namespace: namespace_default,
        channel: void 0,
        type: "REPLY",
        // Actually converting the ID to a string would break communication.
        callId: message.id,
        isError: true,
        ...message.returnValueIsError ? {
          value: message.returnValue,
          isSerializedErrorInstance: true
        } : {
          value: message.returnValue
        }
      };
    }
  }
  throw getUnexpectedMessageError(message);
};
var downgradeMessage = (message) => {
  if (isAck1Message(message)) {
    return {
      penpal: "synAck" /* SynAck */,
      methodNames: message.methodPaths.map(downgradeMethodPath)
    };
  }
  if (isCallMessage(message)) {
    return {
      penpal: "call" /* Call */,
      // Actually converting the ID to a number would break communication.
      id: message.id,
      methodName: downgradeMethodPath(message.methodPath),
      args: message.args
    };
  }
  if (isReplyMessage(message)) {
    if (message.isError) {
      return {
        penpal: "reply" /* Reply */,
        // Actually converting the ID to a number would break communication.
        id: message.callId,
        resolution: "rejected" /* Rejected */,
        ...message.isSerializedErrorInstance ? {
          returnValue: message.value,
          returnValueIsError: true
        } : { returnValue: message.value }
      };
    } else {
      return {
        penpal: "reply" /* Reply */,
        // Actually converting the ID to a number would break communication.
        id: message.callId,
        resolution: "fulfilled" /* Fulfilled */,
        returnValue: message.value
      };
    }
  }
  throw getUnexpectedMessageError(message);
};

// src/shakeHands.ts
var shakeHands = ({
  messenger,
  methods,
  timeout,
  channel,
  log
}) => {
  const participantId = generateId_default();
  let remoteParticipantId;
  const destroyHandlers = [];
  let isComplete = false;
  const methodPaths = extractMethodPathsFromMethods(methods);
  const { promise, resolve, reject } = getPromiseWithResolvers_default();
  const timeoutId = timeout !== void 0 ? setTimeout(() => {
    reject(
      new PenpalError_default(
        "CONNECTION_TIMEOUT",
        `Connection timed out after ${timeout}ms`
      )
    );
  }, timeout) : void 0;
  const destroy = () => {
    for (const destroyHandler of destroyHandlers) {
      destroyHandler();
    }
  };
  const connectCallHandlerAndMethodProxies = () => {
    if (isComplete) {
      return;
    }
    destroyHandlers.push(connectCallHandler_default(messenger, methods, channel, log));
    const { remoteProxy, destroy: destroyMethodProxies } = connectRemoteProxy_default(messenger, channel, log);
    destroyHandlers.push(destroyMethodProxies);
    clearTimeout(timeoutId);
    isComplete = true;
    resolve({
      remoteProxy,
      destroy
    });
  };
  const sendSynMessage = () => {
    const synMessage = {
      namespace: namespace_default,
      type: "SYN",
      channel,
      participantId
    };
    log?.(`Sending handshake SYN`, synMessage);
    try {
      messenger.sendMessage(synMessage);
    } catch (error) {
      reject(new PenpalError_default("TRANSMISSION_FAILED", error.message));
    }
  };
  const handleSynMessage = (message) => {
    log?.(`Received handshake SYN`, message);
    if (message.participantId === remoteParticipantId && // TODO: Used for backward-compatibility. Remove in next major version.
    remoteParticipantId !== DEPRECATED_PENPAL_PARTICIPANT_ID) {
      return;
    }
    remoteParticipantId = message.participantId;
    sendSynMessage();
    const isHandshakeLeader = participantId > remoteParticipantId || // TODO: Used for backward-compatibility. Remove in next major version.
    remoteParticipantId === DEPRECATED_PENPAL_PARTICIPANT_ID;
    if (!isHandshakeLeader) {
      return;
    }
    const ack1Message = {
      namespace: namespace_default,
      channel,
      type: "ACK1",
      methodPaths
    };
    log?.(`Sending handshake ACK1`, ack1Message);
    try {
      messenger.sendMessage(ack1Message);
    } catch (error) {
      reject(new PenpalError_default("TRANSMISSION_FAILED", error.message));
      return;
    }
  };
  const handleAck1Message = (message) => {
    log?.(`Received handshake ACK1`, message);
    const ack2Message = {
      namespace: namespace_default,
      channel,
      type: "ACK2"
    };
    log?.(`Sending handshake ACK2`, ack2Message);
    try {
      messenger.sendMessage(ack2Message);
    } catch (error) {
      reject(new PenpalError_default("TRANSMISSION_FAILED", error.message));
      return;
    }
    connectCallHandlerAndMethodProxies();
  };
  const handleAck2Message = (message) => {
    log?.(`Received handshake ACK2`, message);
    connectCallHandlerAndMethodProxies();
  };
  const handleMessage = (message) => {
    if (isSynMessage(message)) {
      handleSynMessage(message);
    }
    if (isAck1Message(message)) {
      handleAck1Message(message);
    }
    if (isAck2Message(message)) {
      handleAck2Message(message);
    }
  };
  messenger.addMessageHandler(handleMessage);
  destroyHandlers.push(() => messenger.removeMessageHandler(handleMessage));
  sendSynMessage();
  return promise;
};
var shakeHands_default = shakeHands;

// src/once.ts
var once = (fn) => {
  let isCalled = false;
  let result;
  return (...args) => {
    if (!isCalled) {
      isCalled = true;
      result = fn(...args);
    }
    return result;
  };
};
var once_default = once;

// src/connect.ts
var usedMessengers = /* @__PURE__ */ new WeakSet();
var connect = ({
  messenger,
  methods = {},
  timeout,
  channel,
  log
}) => {
  if (!messenger) {
    throw new PenpalError_default("INVALID_ARGUMENT", "messenger must be defined");
  }
  if (usedMessengers.has(messenger)) {
    throw new PenpalError_default(
      "INVALID_ARGUMENT",
      "A messenger can only be used for a single connection"
    );
  }
  usedMessengers.add(messenger);
  const connectionDestroyedHandlers = [messenger.destroy];
  const destroyConnection = once_default((notifyOtherParticipant) => {
    if (notifyOtherParticipant) {
      const destroyMessage = {
        namespace: namespace_default,
        channel,
        type: "DESTROY"
      };
      try {
        messenger.sendMessage(destroyMessage);
      } catch (_) {
      }
    }
    for (const connectionDestroyedHandler of connectionDestroyedHandlers) {
      connectionDestroyedHandler();
    }
    log?.("Connection destroyed");
  });
  const validateReceivedMessage = (data) => {
    return isMessage(data) && data.channel === channel;
  };
  const promise = (async () => {
    try {
      messenger.initialize({ log, validateReceivedMessage });
      messenger.addMessageHandler((message) => {
        if (isDestroyMessage(message)) {
          destroyConnection(false);
        }
      });
      const { remoteProxy, destroy } = await shakeHands_default({
        messenger,
        methods,
        timeout,
        channel,
        log
      });
      connectionDestroyedHandlers.push(destroy);
      return remoteProxy;
    } catch (error) {
      destroyConnection(true);
      throw error;
    }
  })();
  return {
    promise,
    // Why we don't reject the connection promise when consumer calls destroy():
    // https://github.com/Aaronius/penpal/issues/51
    destroy: () => {
      destroyConnection(true);
    }
  };
};
var connect_default = connect;

// src/messengers/WindowMessenger.ts
var WindowMessenger = class {
  #remoteWindow;
  #allowedOrigins;
  #log;
  #validateReceivedMessage;
  #concreteRemoteOrigin;
  #messageCallbacks = /* @__PURE__ */ new Set();
  #port;
  // TODO: Used for backward-compatibility. Remove in next major version.
  #isChildUsingDeprecatedProtocol = false;
  constructor({ remoteWindow, allowedOrigins }) {
    if (!remoteWindow) {
      throw new PenpalError_default("INVALID_ARGUMENT", "remoteWindow must be defined");
    }
    this.#remoteWindow = remoteWindow;
    this.#allowedOrigins = allowedOrigins?.length ? allowedOrigins : [window.origin];
  }
  initialize = ({
    log,
    validateReceivedMessage
  }) => {
    this.#log = log;
    this.#validateReceivedMessage = validateReceivedMessage;
    window.addEventListener("message", this.#handleMessageFromRemoteWindow);
  };
  sendMessage = (message, transferables) => {
    if (isSynMessage(message)) {
      const originForSending = this.#getOriginForSendingMessage(message);
      this.#remoteWindow.postMessage(message, {
        targetOrigin: originForSending,
        transfer: transferables
      });
      return;
    }
    if (isAck1Message(message) || // If the child is using a previous version of Penpal, we need to
    // downgrade the message and send it through the window rather than
    // the port because older versions of Penpal don't use MessagePorts.
    this.#isChildUsingDeprecatedProtocol) {
      const payload = this.#isChildUsingDeprecatedProtocol ? downgradeMessage(message) : message;
      const originForSending = this.#getOriginForSendingMessage(message);
      this.#remoteWindow.postMessage(payload, {
        targetOrigin: originForSending,
        transfer: transferables
      });
      return;
    }
    if (isAck2Message(message)) {
      const { port1, port2 } = new MessageChannel();
      this.#port = port1;
      port1.addEventListener("message", this.#handleMessageFromPort);
      port1.start();
      const transferablesToSend = [port2, ...transferables || []];
      const originForSending = this.#getOriginForSendingMessage(message);
      this.#remoteWindow.postMessage(message, {
        targetOrigin: originForSending,
        transfer: transferablesToSend
      });
      return;
    }
    if (this.#port) {
      this.#port.postMessage(message, {
        transfer: transferables
      });
      return;
    }
    throw new PenpalError_default(
      "TRANSMISSION_FAILED",
      "Cannot send message because the MessagePort is not connected"
    );
  };
  addMessageHandler = (callback) => {
    this.#messageCallbacks.add(callback);
  };
  removeMessageHandler = (callback) => {
    this.#messageCallbacks.delete(callback);
  };
  destroy = () => {
    window.removeEventListener("message", this.#handleMessageFromRemoteWindow);
    this.#destroyPort();
    this.#messageCallbacks.clear();
  };
  #isAllowedOrigin = (origin) => {
    return this.#allowedOrigins.some(
      (allowedOrigin) => allowedOrigin instanceof RegExp ? allowedOrigin.test(origin) : allowedOrigin === origin || allowedOrigin === "*"
    );
  };
  #getOriginForSendingMessage = (message) => {
    if (isSynMessage(message)) {
      return "*";
    }
    if (!this.#concreteRemoteOrigin) {
      throw new PenpalError_default(
        "TRANSMISSION_FAILED",
        "Cannot send message because the remote origin is not established"
      );
    }
    return this.#concreteRemoteOrigin === "null" && this.#allowedOrigins.includes("*") ? "*" : this.#concreteRemoteOrigin;
  };
  #destroyPort = () => {
    this.#port?.removeEventListener("message", this.#handleMessageFromPort);
    this.#port?.close();
    this.#port = void 0;
  };
  #handleMessageFromRemoteWindow = ({
    source,
    origin,
    ports,
    data
  }) => {
    if (source !== this.#remoteWindow) {
      return;
    }
    if (isDeprecatedMessage(data)) {
      this.#log?.(
        "Please upgrade the child window to the latest version of Penpal."
      );
      this.#isChildUsingDeprecatedProtocol = true;
      try {
        data = upgradeMessage(data);
      } catch (error) {
        this.#log?.(
          `Failed to translate deprecated message: ${error.message}`
        );
        return;
      }
    }
    if (!this.#validateReceivedMessage?.(data)) {
      return;
    }
    if (!this.#isAllowedOrigin(origin)) {
      this.#log?.(
        `Received a message from origin \`${origin}\` which did not match allowed origins \`[${this.#allowedOrigins.join(", ")}]\``
      );
      return;
    }
    if (isSynMessage(data)) {
      this.#destroyPort();
      this.#concreteRemoteOrigin = origin;
    }
    if (isAck2Message(data) && // Previous versions of Penpal don't use MessagePorts and do all
    // communication through the window.
    !this.#isChildUsingDeprecatedProtocol) {
      this.#port = ports[0];
      if (!this.#port) {
        this.#log?.("Ignoring ACK2 because it did not include a MessagePort");
        return;
      }
      this.#port.addEventListener("message", this.#handleMessageFromPort);
      this.#port.start();
    }
    for (const callback of this.#messageCallbacks) {
      callback(data);
    }
  };
  #handleMessageFromPort = ({ data }) => {
    if (!this.#validateReceivedMessage?.(data)) {
      return;
    }
    for (const callback of this.#messageCallbacks) {
      callback(data);
    }
  };
};
var WindowMessenger_default = WindowMessenger;

// src/messengers/WorkerMessenger.ts
var WorkerMessenger = class {
  #worker;
  #log;
  #validateReceivedMessage;
  #messageCallbacks = /* @__PURE__ */ new Set();
  #port;
  constructor({ worker }) {
    if (!worker) {
      throw new PenpalError_default("INVALID_ARGUMENT", "worker must be defined");
    }
    this.#worker = worker;
  }
  initialize = ({
    log,
    validateReceivedMessage
  }) => {
    this.#log = log;
    this.#validateReceivedMessage = validateReceivedMessage;
    this.#worker.addEventListener("message", this.#handleMessage);
  };
  sendMessage = (message, transferables) => {
    if (isSynMessage(message) || isAck1Message(message)) {
      this.#worker.postMessage(message, { transfer: transferables });
      return;
    }
    if (isAck2Message(message)) {
      const { port1, port2 } = new MessageChannel();
      this.#port = port1;
      port1.addEventListener("message", this.#handleMessage);
      port1.start();
      this.#worker.postMessage(message, {
        transfer: [port2, ...transferables || []]
      });
      return;
    }
    if (this.#port) {
      this.#port.postMessage(message, {
        transfer: transferables
      });
      return;
    }
    throw new PenpalError_default(
      "TRANSMISSION_FAILED",
      "Cannot send message because the MessagePort is not connected"
    );
  };
  addMessageHandler = (callback) => {
    this.#messageCallbacks.add(callback);
  };
  removeMessageHandler = (callback) => {
    this.#messageCallbacks.delete(callback);
  };
  destroy = () => {
    this.#worker.removeEventListener("message", this.#handleMessage);
    this.#destroyPort();
    this.#messageCallbacks.clear();
  };
  #destroyPort = () => {
    this.#port?.removeEventListener("message", this.#handleMessage);
    this.#port?.close();
    this.#port = void 0;
  };
  #handleMessage = ({ ports, data }) => {
    if (!this.#validateReceivedMessage?.(data)) {
      return;
    }
    if (isSynMessage(data)) {
      this.#destroyPort();
    }
    if (isAck2Message(data)) {
      this.#port = ports[0];
      if (!this.#port) {
        this.#log?.("Ignoring ACK2 because it did not include a MessagePort");
        return;
      }
      this.#port.addEventListener("message", this.#handleMessage);
      this.#port.start();
    }
    for (const callback of this.#messageCallbacks) {
      callback(data);
    }
  };
};
var WorkerMessenger_default = WorkerMessenger;

// src/messengers/PortMessenger.ts
var PortMessenger = class {
  #port;
  #validateReceivedMessage;
  #messageCallbacks = /* @__PURE__ */ new Set();
  constructor({ port }) {
    if (!port) {
      throw new PenpalError_default("INVALID_ARGUMENT", "port must be defined");
    }
    this.#port = port;
  }
  initialize = ({ validateReceivedMessage }) => {
    this.#validateReceivedMessage = validateReceivedMessage;
    this.#port.addEventListener("message", this.#handleMessage);
    this.#port.start();
  };
  sendMessage = (message, transferables) => {
    this.#port?.postMessage(message, {
      transfer: transferables
    });
  };
  addMessageHandler = (callback) => {
    this.#messageCallbacks.add(callback);
  };
  removeMessageHandler = (callback) => {
    this.#messageCallbacks.delete(callback);
  };
  destroy = () => {
    this.#port.removeEventListener("message", this.#handleMessage);
    this.#port.close();
    this.#messageCallbacks.clear();
  };
  #handleMessage = ({ data }) => {
    if (!this.#validateReceivedMessage?.(data)) {
      return;
    }
    for (const callback of this.#messageCallbacks) {
      callback(data);
    }
  };
};
var PortMessenger_default = PortMessenger;

// src/ErrorCodeObj.ts
var ErrorCodeObj = {
  ConnectionDestroyed: "CONNECTION_DESTROYED",
  ConnectionTimeout: "CONNECTION_TIMEOUT",
  InvalidArgument: "INVALID_ARGUMENT",
  MethodCallTimeout: "METHOD_CALL_TIMEOUT",
  MethodNotFound: "METHOD_NOT_FOUND",
  TransmissionFailed: "TRANSMISSION_FAILED"
};
var ErrorCodeObj_default = ErrorCodeObj;

// src/debug.ts
var debug = (prefix) => {
  return (...args) => {
    console.log(`\u270D\uFE0F %c${prefix}%c`, "font-weight: bold;", "", ...args);
  };
};
var debug_default = debug;

export { CallOptions_default as CallOptions, ErrorCodeObj_default as ErrorCode, PenpalError_default as PenpalError, PortMessenger_default as PortMessenger, Reply_default as Reply, WindowMessenger_default as WindowMessenger, WorkerMessenger_default as WorkerMessenger, connect_default as connect, debug_default as debug };
