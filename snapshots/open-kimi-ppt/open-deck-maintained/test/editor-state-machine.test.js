import assert from "node:assert/strict";
import test from "node:test";
import { buildWritablePathAliases } from "../editor/lib.js";
import {
  callRemote,
  createRemoteRpc,
} from "../editor/remote-rpc.js";
import {
  abortDiscardReload,
  assertSaveQueueSwitchClean,
  authorizeDiscardReload,
  cancelSaveQueueSwitch,
  commitDiscardReload,
  enqueueSave,
  finishSaveQueueSwitch,
  freezeAndDrainSaveQueue,
  hasUncertainSaveState,
  MAX_PENDING_SAVES,
  MAX_PENDING_SAVE_BYTES,
  poisonRemoteSession,
  requiresDiscardReload,
  resetSaveLifecycle,
  restoreEditingAfterSaveDrain,
  shouldWarnBeforeUnload,
} from "../editor/save-lifecycle.js";
import {
  collectSaveAttemptPaths,
  MAX_SAVE_FROM_BYTES,
  MAX_SAVE_PPTD_PATH_BYTES,
  MAX_SAVE_SLIDE_ID_BYTES,
  MAX_SAVE_TITLE_BYTES,
  measureSavePayloadBytes,
  sanitizeSavePayload,
  withSavePreflight,
} from "../editor/save-plan.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function nextTurn() {
  return new Promise((resolve) => setImmediate(resolve));
}

function createLifecycleState(overrides = {}) {
  return {
    saveQueue: Promise.resolve(),
    saveFailure: null,
    pendingSaveCount: 0,
    pendingSaveBytes: 0,
    saveBlocked: false,
    remoteUncertainty: null,
    switchEpoch: 0,
    switchLease: null,
    switchPoison: null,
    discardReloadAuthorization: null,
    preparingSwitch: false,
    switching: false,
    readOnlyFallback: false,
    revision: 3,
    ...overrides,
  };
}

function createSaveHarness(state, persistChanges, hooks = {}) {
  return (payload) => enqueueSave(state, payload, {
    captureContext: () => Object.freeze({ revision: state.revision }),
    getAttempt: (savePayload) => ({
      paths: new Set(savePayload.paths ?? []),
      unscoped: Boolean(savePayload.unscoped),
    }),
    isActiveContext: (context) => context.revision === state.revision,
    persistChanges,
    ...hooks,
  });
}

test("freezes the remote gate before draining saves accepted by the freeze reply", async () => {
  const state = createLifecycleState();
  const freezeReply = deferred();
  const saveWrite = deferred();
  const calls = [];
  const onSave = createSaveHarness(state, async () => {
    calls.push("save:start");
    await saveWrite.promise;
    calls.push("save:end");
    return { ok: true };
  });
  let acceptedSave;
  const remote = {
    setEditable(value) {
      calls.push(`editable:${value}`);
      if (value === false) {
        // Model an ordered Penpal onSave delivered before setEditable(false)
        // receives its reply.
        acceptedSave = onSave({ paths: ["pages/01.page"] });
        return freezeReply.promise;
      }
      return Promise.resolve();
    },
  };

  let switchSettled = false;
  const switching = freezeAndDrainSaveQueue(state, remote).then((lease) => {
    switchSettled = true;
    return lease;
  });
  assert.equal(state.preparingSwitch, true);
  assert.equal(state.switching, false);
  assert.equal(state.pendingSaveCount, 1);
  assert.deepEqual(calls, ["editable:false"]);

  await Promise.resolve();
  assert.deepEqual(calls, ["editable:false", "save:start"]);
  freezeReply.resolve();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(state.switching, true);
  assert.equal(switchSettled, false, "switch must wait for the accepted save queue");

  saveWrite.resolve();
  await acceptedSave;
  const lease = await switching;
  assert.deepEqual(calls, ["editable:false", "save:start", "save:end"]);
  assert.equal(state.pendingSaveCount, 0);
  assert.equal(finishSaveQueueSwitch(state, lease), true);
});

test("opens the save gate before restoring remote editing but keeps the switch mutex", async () => {
  const lease = Object.freeze({ epoch: 1, phase: "switch" });
  const state = createLifecycleState({
    switchEpoch: 1,
    switchLease: lease,
    preparingSwitch: true,
    switching: true,
  });
  let persisted = 0;
  const onSave = createSaveHarness(state, async () => {
    persisted += 1;
    return { ok: true };
  });
  let acceptedSave;
  const remote = {
    setEditable(value) {
      assert.equal(value, true);
      assert.equal(state.switching, false, "the local save gate must open first");
      assert.equal(state.preparingSwitch, true, "the deck-switch mutex must remain held");
      acceptedSave = onSave({ paths: ["pages/01.page"] });
      return Promise.resolve();
    },
  };

  await restoreEditingAfterSaveDrain(state, remote, true, lease);
  await acceptedSave;
  assert.equal(persisted, 1);
  await assert.rejects(
    freezeAndDrainSaveQueue(state, remote),
    /另一个文稿切换操作仍在进行/,
  );
  assert.equal(finishSaveQueueSwitch(state, lease), true);
});

test("a failed save discovered while draining aborts the switch and thaws editing", async () => {
  const state = createLifecycleState();
  const failure = new Error("disk full");
  const thaw = deferred();
  let queuedSave;
  const onSave = createSaveHarness(state, async () => {
    throw failure;
  });
  const editable = [];
  const remote = {
    async setEditable(value) {
      editable.push(value);
      if (value === false) {
        queuedSave = onSave({ paths: ["pages/01.page"] });
        return;
      }
      return thaw.promise;
    },
  };

  const switching = freezeAndDrainSaveQueue(state, remote);
  await assert.rejects(queuedSave, /disk full/);
  await Promise.resolve();
  assert.equal(state.switching, false, "saves reopen before the remote thaw");
  assert.equal(state.preparingSwitch, true, "the switch mutex covers the remote thaw");
  await assert.rejects(
    freezeAndDrainSaveQueue(state, remote),
    /另一个文稿切换操作仍在进行/,
  );
  thaw.resolve();
  await assert.rejects(switching, /disk full/);
  assert.deepEqual(editable, [false, true]);
  assert.equal(state.preparingSwitch, false);
  assert.equal(state.switching, false);
  assert.equal(state.saveFailure.error, failure);
});

test("an uncertain freeze permanently poisons its session without same-session thaw", async () => {
  const state = createLifecycleState();
  const editable = [];
  const remote = {
    async setEditable(value) {
      editable.push(value);
      if (value === false) throw new Error("freeze reply lost");
    },
  };

  await assert.rejects(
    freezeAndDrainSaveQueue(state, remote),
    /freeze reply lost/,
  );
  assert.deepEqual(editable, [false]);
  assert.equal(state.saveBlocked, true);
  assert.ok(state.remoteUncertainty);
  assert.equal(state.switchLease, null);
  assert.equal(state.preparingSwitch, false);
  assert.equal(state.switching, false);
});

test("a failed thaw after confirmed freeze leaves saves blocked", async () => {
  const state = createLifecycleState();
  const failure = new Error("disk full");
  const editable = [];
  let queuedSave;
  const onSave = createSaveHarness(state, async () => {
    throw failure;
  });
  const remote = {
    async setEditable(value) {
      editable.push(value);
      if (value === false) {
        queuedSave = onSave({ paths: ["pages/01.page"] });
        return;
      }
      throw new Error("thaw reply lost");
    },
  };

  const switching = freezeAndDrainSaveQueue(state, remote);
  await assert.rejects(queuedSave, /disk full/);
  await assert.rejects(
    switching,
    /disk full/,
  );
  assert.deepEqual(editable, [false, true]);
  assert.equal(state.saveBlocked, true);
  assert.equal(requiresDiscardReload(state), true);
  assert.equal(state.switchLease, null);
  assert.equal(state.preparingSwitch, false);
  assert.equal(state.switching, false);
});

test("freeze timeout is bounded and invalidates the session without thaw", async () => {
  const state = createLifecycleState();
  const editable = [];
  const invalidations = [];
  const remote = {
    setEditable(value) {
      editable.push(value);
      if (value === false) return new Promise(() => undefined);
      return Promise.resolve();
    },
  };

  await assert.rejects(
    freezeAndDrainSaveQueue(state, remote, {
      rpcTimeoutMs: 8,
      onRemoteUncertain: (error) => invalidations.push(error.code),
    }),
    (error) => (
      error.code === "METHOD_CALL_TIMEOUT"
      && error.remoteMayHaveApplied === true
      && error.remoteSessionUnusable === true
    ),
  );
  assert.deepEqual(editable, [false]);
  assert.deepEqual(invalidations, ["METHOD_CALL_TIMEOUT"]);
  assert.equal(state.saveBlocked, true);
  assert.ok(state.remoteUncertainty);
  assert.equal(state.switchLease, null);
  await assert.rejects(
    freezeAndDrainSaveQueue(state, remote, { rpcTimeoutMs: 8 }),
    /确认丢弃更改并重新载入/,
  );
});

test("old-session freeze timeout and failed fresh recovery remain discard-only", async () => {
  const state = createLifecycleState();
  const remote = {
    setEditable() {
      return new Promise(() => undefined);
    },
  };
  const startedAt = Date.now();

  await assert.rejects(
    freezeAndDrainSaveQueue(state, remote, { rpcTimeoutMs: 8 }),
    (error) => error.code === "METHOD_CALL_TIMEOUT",
  );
  assert.ok(Date.now() - startedAt < 250, "both RPCs must leave within their deadlines");
  assert.equal(state.saveBlocked, true);
  assert.ok(state.remoteUncertainty);
  assert.equal(requiresDiscardReload(state), true);
  assert.equal(state.switchLease, null);
  await assert.rejects(
    freezeAndDrainSaveQueue(state, remote, { rpcTimeoutMs: 8 }),
    /确认丢弃更改并重新载入/,
  );

  const authorization = authorizeDiscardReload(state, { confirmed: true });
  const failedFreshRemote = {
    setEditable() {
      return new Promise(() => undefined);
    },
  };
  await assert.rejects(
    freezeAndDrainSaveQueue(
      state,
      failedFreshRemote,
      { discardAuthorization: authorization, rpcTimeoutMs: 8 },
    ),
    (error) => error.code === "METHOD_CALL_TIMEOUT",
  );
  assert.equal(abortDiscardReload(state, authorization), true);
  assert.equal(state.saveBlocked, true, "failed recovery must remain discard-only");
  assert.ok(Date.now() - startedAt < 250, "both sessions must leave within their deadlines");
});

test("thaw timeout blocks subsequent saves until an explicit discard reload", async () => {
  const state = createLifecycleState();
  const remote = {
    setEditable(value) {
      return value === false
        ? Promise.resolve()
        : new Promise(() => undefined);
    },
  };
  const lease = await freezeAndDrainSaveQueue(
    state,
    remote,
    { rpcTimeoutMs: 8 },
  );

  await assert.rejects(
    restoreEditingAfterSaveDrain(
      state,
      remote,
      true,
      lease,
      { rpcTimeoutMs: 8 },
    ),
    (error) => error.code === "METHOD_CALL_TIMEOUT",
  );
  assert.equal(state.saveBlocked, true);
  assert.equal(finishSaveQueueSwitch(state, lease), true);

  let persisted = 0;
  const onSave = createSaveHarness(state, async () => {
    persisted += 1;
  });
  await assert.rejects(onSave({ paths: ["pages/01.page"] }), /尚未确认/);
  assert.equal(persisted, 0);
  await assert.rejects(
    freezeAndDrainSaveQueue(state, remote, { rpcTimeoutMs: 8 }),
    /确认丢弃更改并重新载入/,
  );

  const authorization = authorizeDiscardReload(state, { confirmed: true });
  const recoveryLease = await freezeAndDrainSaveQueue(
    state,
    { setEditable: async () => undefined },
    { discardAuthorization: authorization, rpcTimeoutMs: 8 },
  );
  assert.equal(commitDiscardReload(state, authorization), true);
  assert.equal(finishSaveQueueSwitch(state, recoveryLease), true);
  assert.equal(state.saveBlocked, false);
  assert.equal(state.remoteUncertainty, null);
  await onSave({ paths: ["pages/01.page"] });
  assert.equal(persisted, 1, "confirmed recovery may reopen persistence");
});

test("late freeze success cannot revive its poisoned session", async () => {
  const state = createLifecycleState();
  const lateFreeze = deferred();
  const remote = {
    setEditable(value) {
      if (value === true) return Promise.resolve();
      return lateFreeze.promise;
    },
  };

  await assert.rejects(
    freezeAndDrainSaveQueue(state, remote, { rpcTimeoutMs: 8 }),
    (error) => error.code === "METHOD_CALL_TIMEOUT",
  );
  lateFreeze.resolve();
  await nextTurn();
  assert.equal(state.switchLease, null);
  assert.equal(state.switching, false);
  assert.equal(state.saveBlocked, true);

  const authorization = authorizeDiscardReload(state, { confirmed: true });
  const freshLease = await freezeAndDrainSaveQueue(
    state,
    { setEditable: async () => undefined },
    { discardAuthorization: authorization, rpcTimeoutMs: 8 },
  );
  assert.equal(commitDiscardReload(state, authorization), true);
  assert.equal(finishSaveQueueSwitch(state, freshLease), true);
  assert.equal(state.saveBlocked, false);
});

test("bounded RPC uses branded call options and consumes late failures", async () => {
  class FakeCallOptions {
    constructor(options) {
      this.timeout = options.timeout;
    }
  }
  const lateCommit = deferred();
  const seenOptions = [];
  const remote = {
    setPPTD(_id, _payload, options) {
      seenOptions.push(options);
      return lateCommit.promise;
    },
    getSlideStatus(options) {
      seenOptions.push(options);
      return new Promise(() => undefined);
    },
  };
  const invokeRemote = createRemoteRpc({
    CallOptionsClass: FakeCallOptions,
    defaultTimeoutMs: 8,
  });

  await assert.rejects(
    invokeRemote(remote, "setPPTD", ["deck", {}]),
    (error) => (
      error.code === "METHOD_CALL_TIMEOUT"
      && error.remoteMayHaveApplied === true
      && error.remoteSessionUnusable === true
    ),
  );
  lateCommit.reject(new Error("late setPPTD failure"));
  await nextTurn();

  await assert.rejects(
    invokeRemote(
      remote,
      "getSlideStatus",
      [],
      { mayHaveApplied: false },
    ),
    (error) => (
      error.code === "METHOD_CALL_TIMEOUT"
      && error.remoteMayHaveApplied === false
      && error.remoteSessionUnusable === true
    ),
  );
  assert.equal(seenOptions.length, 2);
  assert.ok(seenOptions.every((value) => value instanceof FakeCallOptions));
  assert.deepEqual(seenOptions.map((value) => value.timeout), [8, 8]);
});

test("timed-out setPPTD cannot be recovered or thawed on the same session", async () => {
  const state = createLifecycleState();
  const lateNewDeck = deferred();
  let oldSessionCurrent = true;
  let oldIframeDeck = "old";
  let sameSessionRecoveryCalls = 0;
  const oldRemote = {
    setPPTD(id) {
      if (id === "new") {
        return lateNewDeck.promise.then(() => {
          oldIframeDeck = "new";
        });
      }
      sameSessionRecoveryCalls += 1;
      oldIframeDeck = "old";
      return Promise.resolve();
    },
  };
  const oldInvoke = (remote, method, args, options = {}) => callRemote(
    remote,
    method,
    args,
    {
      timeoutMs: 8,
      isSessionCurrent: () => oldSessionCurrent,
      ...options,
    },
  );

  let uncertainError;
  await assert.rejects(
    oldInvoke(oldRemote, "setPPTD", ["new", {}]).catch((error) => {
      uncertainError = error;
      poisonRemoteSession(state, error);
      oldSessionCurrent = false;
      throw error;
    }),
    (error) => error.code === "METHOD_CALL_TIMEOUT",
  );
  assert.equal(state.saveBlocked, true);
  assert.ok(state.remoteUncertainty);

  await assert.rejects(
    oldInvoke(oldRemote, "setPPTD", ["old", {}]),
    (error) => error.code === "REMOTE_RPC_SESSION_EXPIRED",
  );
  assert.equal(sameSessionRecoveryCalls, 0, "old-session compensation must not be sent");

  lateNewDeck.resolve();
  await nextTurn();
  assert.equal(oldIframeDeck, "new", "the timed-out side effect may still land remotely");
  assert.equal(oldSessionCurrent, false);
  assert.equal(state.saveBlocked, true, "late apply must not reopen local capabilities");
  assert.equal(state.remoteUncertainty.error, uncertainError);

  const freshEditable = [];
  let freshIframeDeck = "empty";
  const freshRemote = {
    async setEditable(value) {
      freshEditable.push(value);
    },
    async setPPTD(id) {
      freshIframeDeck = id;
    },
  };
  const authorization = authorizeDiscardReload(state, { confirmed: true });
  const freshLease = await freezeAndDrainSaveQueue(
    state,
    freshRemote,
    { discardAuthorization: authorization, rpcTimeoutMs: 8 },
  );
  await callRemote(
    freshRemote,
    "setPPTD",
    ["old", {}],
    { timeoutMs: 8 },
  );
  assert.equal(commitDiscardReload(state, authorization), true);
  await restoreEditingAfterSaveDrain(
    state,
    freshRemote,
    true,
    freshLease,
    { rpcTimeoutMs: 8 },
  );
  assert.equal(finishSaveQueueSwitch(state, freshLease), true);
  assert.equal(freshIframeDeck, "old");
  assert.deepEqual(freshEditable, [false, true]);
  assert.equal(state.saveBlocked, false);
  assert.equal(state.remoteUncertainty, null);
});

test("a reply from an expired remote session is rejected without becoming current", async () => {
  const reply = deferred();
  let current = true;
  const pending = callRemote(
    { getSlideStatus: () => reply.promise },
    "getSlideStatus",
    [],
    {
      timeoutMs: 100,
      mayHaveApplied: false,
      isSessionCurrent: () => current,
    },
  );
  current = false;
  reply.resolve({ page: 99 });

  await assert.rejects(
    pending,
    (error) => (
      error.code === "REMOTE_RPC_SESSION_EXPIRED"
      && error.remoteMayHaveApplied === false
    ),
  );
});

test("cancel owns a newer epoch so a stale freeze continuation cannot close the gate", async () => {
  const state = createLifecycleState();
  const freezeReply = deferred();
  const thawReply = deferred();
  const editable = [];
  const remote = {
    setEditable(value) {
      editable.push(value);
      return value ? thawReply.promise : freezeReply.promise;
    },
  };

  const switching = freezeAndDrainSaveQueue(state, remote);
  const originalLease = state.switchLease;
  assert.equal(originalLease.phase, "switch");
  const switchingRejected = assert.rejects(switching, /凭据已经过期/);
  const cancelling = cancelSaveQueueSwitch(state, remote, { lease: originalLease });

  assert.notEqual(state.switchLease, originalLease);
  assert.equal(state.switchLease.phase, "cancel");
  assert.equal(state.preparingSwitch, true);
  assert.equal(state.switching, false);
  assert.deepEqual(
    editable,
    [false],
    "cancel must wait for the superseded freeze before issuing the final thaw",
  );
  freezeReply.resolve();
  await switchingRejected;
  assert.equal(
    state.switching,
    false,
    "the expired freeze reply must not close the cancellation owner's gate",
  );
  assert.equal(state.preparingSwitch, true, "cancel owns the mutex through remote thaw");

  thawReply.resolve();
  assert.equal(await cancelling, true);
  assert.deepEqual(editable, [false, true]);
  assert.equal(state.switchLease, null);
  assert.equal(state.preparingSwitch, false);
  assert.equal(state.switching, false);
});

test("a failed cancellation thaw becomes discard-only instead of reopening saves", async () => {
  const state = createLifecycleState();
  const restoreErrors = [];
  const remote = {
    async setEditable(value) {
      if (value) throw new Error("remote thaw failed");
    },
  };
  const lease = await freezeAndDrainSaveQueue(state, {
    setEditable: async () => undefined,
  });

  assert.equal(
    await cancelSaveQueueSwitch(state, remote, {
      lease,
      onRestoreError: (error) => restoreErrors.push(error.message),
    }),
    true,
  );
  assert.deepEqual(restoreErrors, ["远端 setEditable 调用失败：remote thaw failed"]);
  assert.equal(state.saveBlocked, true);
  assert.equal(state.switching, false);
  assert.equal(state.switchLease, null);
  assert.equal(requiresDiscardReload(state), true);
});

test("late save poisons a draining switch and keeps the remote fail-closed", async () => {
  const drain = deferred();
  const state = createLifecycleState({ saveQueue: drain.promise });
  const editable = [];
  const poisoned = [];
  const remote = {
    async setEditable(value) {
      editable.push(value);
    },
  };
  const onSave = createSaveHarness(state, async () => ({ ok: true }), {
    onSwitchPoison: (error) => poisoned.push(error.code),
  });

  const switching = freezeAndDrainSaveQueue(state, remote);
  const switchRejected = assert.rejects(
    switching,
    (error) => error.code === "SWITCH_POISONED_BY_LATE_SAVE",
  );
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(state.switching, true);
  await assert.rejects(
    onSave({ paths: ["pages/late.page"] }),
    (error) => error.code === "SWITCH_POISONED_BY_LATE_SAVE",
  );
  assert.deepEqual(poisoned, ["SWITCH_POISONED_BY_LATE_SAVE"]);
  assert.equal(state.saveBlocked, true);

  drain.resolve();
  await switchRejected;
  assert.deepEqual(editable, [false], "a poisoned switch must not thaw the iframe");
  assert.equal(state.switchLease, null);
  assert.equal(state.switching, false);
  assert.equal(requiresDiscardReload(state), true);
});

test("late save is detected before a remote deck commit can start", async () => {
  const state = createLifecycleState();
  const remote = { setEditable: async () => undefined };
  const lease = await freezeAndDrainSaveQueue(state, remote);
  const onSave = createSaveHarness(state, async () => ({ ok: true }));

  await assert.rejects(
    onSave({ paths: ["pages/late.page"] }),
    /迟到的保存请求/,
  );
  let setPPTDStarted = false;
  assert.throws(
    () => {
      assertSaveQueueSwitchClean(state, lease);
      setPPTDStarted = true;
    },
    (error) => error.code === "SWITCH_POISONED_BY_LATE_SAVE",
  );
  assert.equal(setPPTDStarted, false);
  assert.equal(state.saveBlocked, true);
  assert.equal(finishSaveQueueSwitch(state, lease), true);
});

test("late save while remote commit is in flight forces old-deck recovery and no commit", async () => {
  const state = createLifecycleState();
  const remoteCommit = deferred();
  const remote = { setEditable: async () => undefined };
  const lease = await freezeAndDrainSaveQueue(state, remote);
  const onSave = createSaveHarness(state, async () => ({ ok: true }));
  let mayHaveAppliedNewDeck = false;
  let hostCommitted = false;
  let oldDeckRecovered = false;

  const transaction = (async () => {
    try {
      assertSaveQueueSwitchClean(state, lease);
      mayHaveAppliedNewDeck = true;
      await remoteCommit.promise;
      assertSaveQueueSwitchClean(state, lease);
      hostCommitted = true;
    } catch (error) {
      if (mayHaveAppliedNewDeck) oldDeckRecovered = true;
      throw error;
    } finally {
      finishSaveQueueSwitch(state, lease);
    }
  })();
  const transactionRejected = assert.rejects(
    transaction,
    (error) => error.code === "SWITCH_POISONED_BY_LATE_SAVE",
  );

  await Promise.resolve();
  assert.equal(mayHaveAppliedNewDeck, true);
  await assert.rejects(
    onSave({ paths: ["pages/late.page"] }),
    /迟到的保存请求/,
  );
  remoteCommit.resolve();
  await transactionRejected;

  assert.equal(hostCommitted, false);
  assert.equal(oldDeckRecovered, true);
  assert.equal(state.saveBlocked, true);
  assert.equal(state.switching, false);
  assert.equal(state.switchLease, null);
});

test("uncertain save state cannot clear without confirmed discard and reload", async () => {
  const originalFailure = {
    revision: 3,
    error: new Error("uncertain editor state"),
    paths: new Set(["pages/01.page"]),
    unscoped: true,
  };
  const state = createLifecycleState({
    saveBlocked: true,
    saveFailure: originalFailure,
  });
  let persisted = 0;
  const onSave = createSaveHarness(state, async () => {
    persisted += 1;
    return { ok: true };
  });

  await assert.rejects(onSave({ paths: ["pages/01.page"] }), /尚未确认/);
  assert.equal(persisted, 0);
  assert.equal(requiresDiscardReload(state), true);
  assert.throws(
    () => resetSaveLifecycle(state),
    /用户确认丢弃更改并重新载入/,
  );
  assert.equal(authorizeDiscardReload(state), null);
  assert.equal(state.saveBlocked, true);
  assert.equal(state.saveFailure, originalFailure);

  const failedAuthorization = authorizeDiscardReload(
    state,
    { confirmed: true },
  );
  assert.ok(failedAuthorization);
  assert.equal(state.saveBlocked, true, "confirmation alone must not clear fail-closed state");
  assert.equal(state.saveFailure, originalFailure);
  const failedLease = await freezeAndDrainSaveQueue(
    state,
    { setEditable: async () => undefined },
    { discardAuthorization: failedAuthorization },
  );
  await cancelSaveQueueSwitch(
    state,
    { setEditable: async () => assert.fail("blocked recovery must not thaw") },
    { lease: failedLease },
  );
  assert.equal(abortDiscardReload(state, failedAuthorization), true);
  assert.equal(state.saveBlocked, true, "failed reload must restore blocked state");
  assert.equal(state.saveFailure, originalFailure);

  const successfulAuthorization = authorizeDiscardReload(
    state,
    { confirmed: true },
  );
  const successfulLease = await freezeAndDrainSaveQueue(
    state,
    { setEditable: async () => undefined },
    { discardAuthorization: successfulAuthorization },
  );
  assert.equal(state.saveBlocked, true, "state remains blocked until deck commit");
  assert.equal(commitDiscardReload(state, successfulAuthorization), true);
  assert.equal(state.saveBlocked, false);
  assert.equal(state.saveFailure, null);
  assert.equal(finishSaveQueueSwitch(state, successfulLease), true);
  await onSave({ paths: ["pages/01.page"] });
  assert.equal(persisted, 1);
});

test("failed paths remain sticky across unrelated successes and clear on retry", async () => {
  const state = createLifecycleState();
  const sticky = [];
  const rejected = [];
  const onSave = createSaveHarness(
    state,
    async (payload) => {
      if (payload.fail) throw new Error(payload.fail);
      return { ok: true };
    },
    {
      onStickyFailure: (failure) => sticky.push([...failure.paths]),
      onRejected: (error) => rejected.push(error.message),
    },
  );

  await assert.rejects(
    onSave({ paths: ["pages/01.page"], fail: "page 1 failed" }),
    /page 1 failed/,
  );
  assert.equal(requiresDiscardReload(state), true);
  assert.equal(hasUncertainSaveState(state), false);
  assert.deepEqual([...state.saveFailure.paths], ["pages/01.page"]);
  assert.deepEqual(rejected, ["page 1 failed"]);

  await onSave({ paths: ["pages/02.page"] });
  assert.deepEqual([...state.saveFailure.paths], ["pages/01.page"]);
  assert.deepEqual(sticky, [["pages/01.page"]]);

  await onSave({ paths: ["pages/01.page"] });
  assert.equal(state.saveFailure, null);
  assert.equal(requiresDiscardReload(state), false);
  assert.equal(state.pendingSaveCount, 0);
});

test("a scoped failure can be discarded only by a confirmed successful reload", async () => {
  const originalFailure = {
    revision: 3,
    error: new Error("disk full"),
    paths: new Set(["pages/01.page"]),
    unscoped: false,
  };
  const state = createLifecycleState({ saveFailure: originalFailure });

  assert.equal(requiresDiscardReload(state), true);
  assert.equal(hasUncertainSaveState(state), false);
  assert.equal(authorizeDiscardReload(state, { confirmed: false }), null);

  const cancelledAuthorization = authorizeDiscardReload(
    state,
    { confirmed: true },
  );
  assert.ok(cancelledAuthorization);
  assert.equal(abortDiscardReload(state, cancelledAuthorization), true);
  assert.equal(state.saveFailure, originalFailure);
  assert.equal(state.saveBlocked, false);

  const committedAuthorization = authorizeDiscardReload(
    state,
    { confirmed: true },
  );
  const lease = await freezeAndDrainSaveQueue(
    state,
    { setEditable: async () => undefined },
    { discardAuthorization: committedAuthorization },
  );
  assert.equal(
    state.saveFailure,
    originalFailure,
    "confirmation must preserve the evidence until the new deck commits",
  );
  assert.equal(commitDiscardReload(state, committedAuthorization), true);
  assert.equal(state.saveFailure, null);
  assert.equal(requiresDiscardReload(state), false);
  assert.equal(finishSaveQueueSwitch(state, lease), true);
});

test("aborting a scoped discard cannot clear a newer uncertain freeze result", async () => {
  const originalFailure = {
    revision: 3,
    error: new Error("disk full"),
    paths: new Set(["pages/01.page"]),
    unscoped: false,
  };
  const state = createLifecycleState({ saveFailure: originalFailure });
  const authorization = authorizeDiscardReload(state, { confirmed: true });
  const remote = {
    async setEditable(value) {
      throw new Error(value ? "thaw reply lost" : "freeze reply lost");
    },
  };

  await assert.rejects(
    freezeAndDrainSaveQueue(
      state,
      remote,
      { discardAuthorization: authorization },
    ),
    /freeze reply lost/,
  );
  assert.equal(state.saveBlocked, true);
  assert.equal(abortDiscardReload(state, authorization), true);
  assert.equal(
    state.saveBlocked,
    true,
    "failed recovery must remain fail-closed after the authorization aborts",
  );
  assert.equal(state.saveFailure, originalFailure);
  assert.equal(hasUncertainSaveState(state), true);
});

test("an unscoped failed attempt becomes fail-closed until confirmed discard", async () => {
  const state = createLifecycleState();
  let attempts = 0;
  const onSave = createSaveHarness(state, async () => {
    attempts += 1;
    throw new Error("payload could not be scoped");
  });

  await assert.rejects(
    onSave({ unscoped: true }),
    /payload could not be scoped/,
  );
  assert.equal(attempts, 1);
  assert.equal(state.saveFailure.unscoped, true);
  assert.equal(state.saveBlocked, true);
  assert.equal(requiresDiscardReload(state), true);
  await assert.rejects(onSave({ paths: ["pages/01.page"] }), /尚未确认/);
  assert.equal(attempts, 1, "blocked saves must not reach persistence");

  assert.equal(authorizeDiscardReload(state, { confirmed: false }), null);
  assert.equal(state.saveBlocked, true);
  const authorization = authorizeDiscardReload(state, { confirmed: true });
  assert.ok(authorization);
  assert.equal(state.saveBlocked, true);
  assert.notEqual(state.saveFailure, null);
  assert.equal(abortDiscardReload(state, authorization), true);
  assert.equal(state.saveBlocked, true);
  assert.notEqual(state.saveFailure, null);
});

test("a save whose disk mutation may have applied becomes discard-only", async () => {
  const state = createLifecycleState();
  const error = new Error("post-write verification failed");
  error.saveScopeUncertain = true;
  const onSave = createSaveHarness(state, async () => {
    throw error;
  });

  await assert.rejects(
    onSave({ paths: ["pages/01.page"] }),
    /post-write verification failed/,
  );
  assert.equal(state.saveBlocked, true);
  assert.equal(state.saveFailure.unscoped, true);
  assert.equal(requiresDiscardReload(state), true);
});

test("an uncertain queued save prevents later queued mutations from starting", async () => {
  const state = createLifecycleState();
  const firstStarted = deferred();
  const firstFinish = deferred();
  const calls = [];
  const uncertain = new Error("first mutation uncertain");
  uncertain.saveScopeUncertain = true;
  const onSave = createSaveHarness(state, async (payload) => {
    calls.push(payload.id);
    if (payload.id === "first") {
      firstStarted.resolve();
      await firstFinish.promise;
      throw uncertain;
    }
    throw new Error("second save must never execute");
  });

  const first = onSave({ id: "first", paths: ["pages/01.page"] });
  await firstStarted.promise;
  const second = onSave({ id: "second", paths: ["pages/02.page"] });
  firstFinish.resolve();

  await assert.rejects(first, /first mutation uncertain/);
  await assert.rejects(second, /已取消排队中的后续保存/);
  assert.deepEqual(calls, ["first"]);
  assert.equal(state.saveBlocked, true);
  assert.equal(state.saveFailure.unscoped, true);
});

test("save queue overflow fails closed before retaining or inspecting another payload", async () => {
  const state = createLifecycleState({ pendingSaveCount: MAX_PENDING_SAVES });
  let attemptCalls = 0;
  let persistCalls = 0;
  const onSave = createSaveHarness(
    state,
    async () => {
      persistCalls += 1;
    },
    {
      getAttempt() {
        attemptCalls += 1;
        return { paths: new Set(), unscoped: false };
      },
    },
  );

  await assert.rejects(
    onSave({ changes: [{ path: "pages/overflow.page", content: "x" }] }),
    (error) => error.code === "SAVE_QUEUE_OVERFLOW",
  );
  assert.equal(attemptCalls, 0);
  assert.equal(persistCalls, 0);
  assert.equal(state.pendingSaveCount, MAX_PENDING_SAVES);
  assert.equal(state.saveBlocked, true);
  assert.equal(state.saveFailure.unscoped, true);
});

test("oversized payloads and aggregate queued bytes are rejected before persistence", async () => {
  const limits = {
    maxChangeCount: 600,
    maxFileBytes: 20 * 1024 * 1024,
    maxTotalBytes: 100 * 1024 * 1024,
  };
  let persistCalls = 0;
  const oversizedState = createLifecycleState();
  const oversizedSave = createSaveHarness(
    oversizedState,
    async () => {
      persistCalls += 1;
    },
    {
      getPayloadBytes: (payload) => measureSavePayloadBytes(payload, limits),
      maxPendingSaveBytes: MAX_PENDING_SAVE_BYTES,
    },
  );
  await assert.rejects(
    oversizedSave({
      changes: [{
        path: "pages/huge.page",
        content: "x".repeat(20 * 1024 * 1024 + 1),
      }],
    }),
    (error) => error.code === "SAVE_PAYLOAD_OVERFLOW",
  );
  assert.equal(persistCalls, 0);
  assert.equal(oversizedState.pendingSaveCount, 0);
  assert.equal(oversizedState.pendingSaveBytes, 0);
  assert.equal(oversizedState.saveBlocked, true);

  const queuedState = createLifecycleState({ pendingSaveBytes: 90 });
  let envelopeCalls = 0;
  const queuedSave = createSaveHarness(
    queuedState,
    async () => {
      persistCalls += 1;
    },
    {
      getPayloadBytes() {
        envelopeCalls += 1;
        return 11;
      },
      maxPendingSaveBytes: 100,
    },
  );
  await assert.rejects(
    queuedSave({ changes: [] }),
    (error) => error.code === "SAVE_QUEUE_BYTES_OVERFLOW",
  );
  assert.equal(envelopeCalls, 1);
  assert.equal(persistCalls, 0);
  assert.equal(queuedState.pendingSaveBytes, 90);
  assert.equal(queuedState.saveBlocked, true);
});

test("queued saves retain only a frozen minimal payload envelope", async () => {
  const state = createLifecycleState();
  let persisted;
  const onSave = createSaveHarness(
    state,
    async (payload) => {
      persisted = payload;
      return { ok: true };
    },
    {
      sanitizePayload: (payload) => sanitizeSavePayload(
        payload,
        { maxChangeCount: 600 },
      ),
      getPayloadBytes: (payload) => measureSavePayloadBytes(payload, {
        maxChangeCount: 600,
        maxFileBytes: 20 * 1024 * 1024,
        maxTotalBytes: 100 * 1024 * 1024,
      }),
    },
  );
  const raw = {
    changes: [{
      operate: "update",
      path: "pages/01.page",
      content: "{}",
    }],
    fileContent: [
      { path: "deck.pptd", content: "manifest" },
      { path: "pages/01.page", content: "page" },
    ],
    saveFrom: "text-edit",
    slideId: "slide-01",
    chatId: undefined,
    file: { pptdPath: "deck.pptd" },
    title: "Deck title",
  };
  await onSave(raw);
  assert.deepEqual(Object.keys(persisted).sort(), ["changes", "fileContent"]);
  assert.deepEqual(Object.keys(persisted.changes[0]).sort(), ["content", "operate", "path"]);
  assert.equal(Object.isFrozen(persisted), true);
  assert.equal(Object.isFrozen(persisted.changes), true);
  assert.equal(Object.isFrozen(persisted.changes[0]), true);
  assert.equal(Object.isFrozen(persisted.fileContent), true);
  assert.equal(Object.isFrozen(persisted.fileContent[0]), true);
  const echoOnly = sanitizeSavePayload(
    { fileContent: "echo" },
    { maxChangeCount: 600 },
  );
  assert.deepEqual(echoOnly.changes, []);
  assert.equal(echoOnly.fileContent, "echo");
});

test("current fileContent array schema is cloned, frozen, and fully byte-budgeted", () => {
  const rawFileContent = [
    { path: "deck.pptd", content: "manifest" },
    { path: "pages/01.page", content: "page" },
  ];
  const accepted = sanitizeSavePayload(
    { changes: [], fileContent: rawFileContent },
    { maxChangeCount: 600 },
  );
  assert.deepEqual(accepted.fileContent, rawFileContent);
  assert.notEqual(accepted.fileContent, rawFileContent);
  assert.notEqual(accepted.fileContent[0], rawFileContent[0]);
  assert.equal(Object.isFrozen(accepted.fileContent), true);
  assert.equal(Object.isFrozen(accepted.fileContent[0]), true);
  assert.equal(
    measureSavePayloadBytes(accepted, {
      maxChangeCount: 600,
      maxFileBytes: 20,
      maxTotalBytes: 100,
    }),
    34,
  );

  const expando = [];
  expando.hidden = "not allowed";
  for (const fileContent of [
    new Array(1),
    expando,
    [null],
    [{ path: "deck.pptd" }],
    [{ path: "deck.pptd", content: "manifest", hidden: "not allowed" }],
  ]) {
    assert.throws(
      () => sanitizeSavePayload(
        { changes: [], fileContent },
        { maxChangeCount: 600 },
      ),
      /fileContent/,
    );
  }
  assert.throws(
    () => measureSavePayloadBytes(
      { changes: [], fileContent: [{ path: "deck.pptd", content: "x".repeat(21) }] },
      { maxChangeCount: 600, maxFileBytes: 20, maxTotalBytes: 100 },
    ),
    /保存返回内容超过单项安全上限/,
  );
});

test("known save metadata is bounded and discarded before queue retention", () => {
  const accepted = sanitizeSavePayload(
    {
      changes: [],
      saveFrom: "x".repeat(MAX_SAVE_FROM_BYTES),
      slideId: "x".repeat(MAX_SAVE_SLIDE_ID_BYTES),
      chatId: undefined,
      file: { pptdPath: "x".repeat(MAX_SAVE_PPTD_PATH_BYTES) },
      title: "x".repeat(MAX_SAVE_TITLE_BYTES),
    },
    { maxChangeCount: 600 },
  );
  assert.deepEqual(Object.keys(accepted), ["changes"]);
  assert.equal(Object.hasOwn(accepted, "saveFrom"), false);

  for (const [field, value] of [
    ["saveFrom", { nested: "must not be retained" }],
    ["saveFrom", "x".repeat(MAX_SAVE_FROM_BYTES + 1)],
    ["saveFrom", "中".repeat(Math.floor(MAX_SAVE_FROM_BYTES / 3) + 1)],
    ["slideId", "x".repeat(MAX_SAVE_SLIDE_ID_BYTES + 1)],
    ["chatId", "unexpected-chat"],
    ["file", null],
    ["file", {}],
    ["file", { pptdPath: "deck.pptd", content: "must not be retained" }],
    ["file", { pptdPath: "x".repeat(MAX_SAVE_PPTD_PATH_BYTES + 1) }],
    ["title", "x".repeat(MAX_SAVE_TITLE_BYTES + 1)],
  ]) {
    assert.throws(
      () => sanitizeSavePayload(
        { changes: [], [field]: value },
        { maxChangeCount: 600 },
      ),
      new RegExp(field === "file" ? "file" : field),
    );
  }
});

test("unknown save metadata reports bounded field names without values or types", () => {
  assert.throws(
    () => sanitizeSavePayload(
      {
        changes: [],
        firstUnknown: "secret-value",
        secondUnknown: { nested: "secret-value" },
      },
      { maxChangeCount: 600 },
    ),
    (error) => {
      assert.match(error.message, /firstUnknown, secondUnknown/);
      assert.doesNotMatch(error.message, /secret-value|string|object/);
      return true;
    },
  );
});

test("unknown save fields and delete content fail immediately instead of entering the queue", async () => {
  const changesWithHiddenField = [];
  changesWithHiddenField.hidden = { huge: true };
  for (const payload of [
    {
      changes: [{ operate: "update", path: "pages/01.page", content: "{}" }],
      unknownTopLevel: { huge: true },
    },
    {
      changes: [{
        operate: "update",
        path: "pages/01.page",
        content: "{}",
        unknownChangeField: { huge: true },
      }],
    },
    {
      changes: [{
        operate: "delete",
        path: "pages/01.page",
        content: "must not be silently dropped",
      }],
    },
    { changes: changesWithHiddenField },
  ]) {
    const state = createLifecycleState();
    let persistCalls = 0;
    const onSave = createSaveHarness(
      state,
      async () => {
        persistCalls += 1;
      },
      {
        sanitizePayload: (value) => sanitizeSavePayload(
          value,
          { maxChangeCount: 600 },
        ),
      },
    );
    await assert.rejects(
      onSave(payload),
      /不支持的字段|不能携带 content|changes 数组包含不支持的字段/,
    );
    assert.equal(persistCalls, 0);
    assert.equal(state.pendingSaveCount, 0);
    assert.equal(state.saveBlocked, true);
  }
});

test("cross-attempt save failure evidence is bounded and then becomes discard-only", async () => {
  const state = createLifecycleState();
  const onSave = createSaveHarness(state, async () => {
    throw new Error("persistent failure");
  });

  for (let index = 0; index <= 600; index += 1) {
    await assert.rejects(
      onSave({ paths: [`pages/failure-${index}.page`] }),
      /persistent failure/,
    );
  }
  assert.equal(state.saveFailure.paths.size, 600);
  assert.equal(state.saveFailure.unscoped, true);
  assert.equal(state.saveBlocked, true);
  await assert.rejects(
    onSave({ paths: ["pages/never-runs.page"] }),
    /尚未确认/,
  );
});

test("pending saves trigger beforeunload even before the UI enters saving state", async () => {
  const state = createLifecycleState();
  const write = deferred();
  const onSave = createSaveHarness(state, () => write.promise);

  const saving = onSave({ paths: ["pages/01.page"] });
  assert.equal(state.pendingSaveCount, 1);
  assert.equal(shouldWarnBeforeUnload(state, "saved"), true);

  write.resolve({ ok: true });
  await saving;
  assert.equal(state.pendingSaveCount, 0);
  assert.equal(shouldWarnBeforeUnload(state, "saved"), false);
});

test("capability ceilings fail before the mutation callback can run", () => {
  const manifest = "version: v2\npages:\n  - pages/01.page\n";
  const originalPage = '{"elements":[]}';
  const context = {
    source: "demo",
    manifestPath: "deck.pptd",
    manifestDirectory: "",
    memoryFiles: new Map([
      ["deck.pptd", manifest],
      ["pages/01.page", originalPage],
    ]),
    fileIndex: new Map(),
    writablePathAliases: buildWritablePathAliases("deck.pptd", ["pages/01.page"]),
    imageReferences: new Map([
      ["deck.pptd", []],
      ["pages/01.page", []],
    ]),
    imageGrants: new Set(),
  };
  const payload = {
    changes: [{
      path: "pages/01.page",
      operate: "update",
      content: '{"elements":[{"src":"media/not-granted.png"}]}',
    }],
  };
  const limits = {
    maxChangeCount: 600,
    maxTextBytes: 20 * 1024 * 1024,
    maxSaveBytes: 100 * 1024 * 1024,
  };
  let mutationCalled = false;

  assert.throws(
    () => withSavePreflight(payload, context, limits, () => {
      mutationCalled = true;
      context.memoryFiles.set("pages/01.page", "mutated");
    }),
    /未授权的本地图片/,
  );
  assert.equal(mutationCalled, false);
  assert.equal(context.memoryFiles.get("pages/01.page"), originalPage);
  assert.deepEqual(context.imageReferences.get("pages/01.page"), []);
});

test("save preflight accepts 500 pages but rejects 501 before mutation", () => {
  const pagePaths = Array.from(
    { length: 501 },
    (_, index) => `pages/${String(index + 1).padStart(3, "0")}.page`,
  );
  const acceptedPages = pagePaths.slice(0, 500);
  const manifest = JSON.stringify({ version: "v2", pages: acceptedPages });
  const context = {
    source: "demo",
    manifestPath: "deck.pptd",
    manifestDirectory: "",
    memoryFiles: new Map([
      ["deck.pptd", manifest],
      ...acceptedPages.map((path) => [path, "{}"]),
    ]),
    fileIndex: new Map(),
    writablePathAliases: buildWritablePathAliases("deck.pptd", acceptedPages),
    imageReferences: new Map([["deck.pptd", []]]),
    imageGrants: new Set(),
  };
  const limits = {
    maxChangeCount: 600,
    maxTextBytes: 20 * 1024 * 1024,
    maxSaveBytes: 100 * 1024 * 1024,
    maxPageCount: 500,
  };
  let acceptedMutationCalled = false;
  withSavePreflight({
    changes: [{
      path: "deck.pptd",
      operate: "update",
      content: manifest,
    }],
  }, context, limits, () => {
    acceptedMutationCalled = true;
  });
  assert.equal(acceptedMutationCalled, true);

  let rejectedMutationCalled = false;
  assert.throws(
    () => withSavePreflight({
      changes: [{
        path: "deck.pptd",
        operate: "update",
        content: JSON.stringify({ version: "v2", pages: pagePaths }),
      }],
    }, context, limits, () => {
      rejectedMutationCalled = true;
    }),
    /包含 501 页，超过 500 页安全上限/,
  );
  assert.equal(rejectedMutationCalled, false);
});

test("oversized save attempts become unscoped without traversing changes", () => {
  const changes = new Proxy(new Array(601), {
    get(target, property, receiver) {
      if (property === "length") return 601;
      throw new Error(`unexpected traversal of ${String(property)}`);
    },
  });
  const attempt = collectSaveAttemptPaths(
    { changes },
    { writablePathAliases: new Map() },
    { maxChangeCount: 600 },
  );
  assert.equal(attempt.unscoped, true);
  assert.equal(attempt.paths.size, 0);
});

test("full next-deck text budget is checked before mutation and records exact sizes", () => {
  const encoder = new TextEncoder();
  const manifest = JSON.stringify({ version: "v2", pages: ["pages/01.page"] });
  const nextManifest = JSON.stringify({
    version: "v2",
    pages: ["pages/01.page", "pages/02.page"],
  });
  const pageOne = "一号页面";
  const pageTwo = "second page";
  const context = {
    source: "demo",
    manifestPath: "deck.pptd",
    manifestDirectory: "",
    memoryFiles: new Map([
      ["deck.pptd", manifest],
      ["pages/01.page", pageOne],
    ]),
    textByteSizes: new Map([
      ["deck.pptd", encoder.encode(manifest).byteLength],
      ["pages/01.page", encoder.encode(pageOne).byteLength],
    ]),
    fileIndex: new Map(),
    writablePathAliases: buildWritablePathAliases("deck.pptd", ["pages/01.page"]),
    imageReferences: new Map([
      ["deck.pptd", []],
      ["pages/01.page", []],
    ]),
    imageGrants: new Set(),
  };
  const payload = {
    changes: [
      { path: "deck.pptd", operate: "update", content: nextManifest },
      { path: "pages/02.page", operate: "create", content: pageTwo },
    ],
  };
  const totalBytes = [nextManifest, pageOne, pageTwo]
    .reduce((total, content) => total + encoder.encode(content).byteLength, 0);
  const baseLimits = {
    maxChangeCount: 600,
    maxTextBytes: 1024,
    maxSaveBytes: 2048,
    maxPageCount: 500,
  };

  let rejectedMutationCalled = false;
  assert.throws(
    () => withSavePreflight(
      payload,
      context,
      { ...baseLimits, maxDeckTextBytes: totalBytes - 1 },
      () => {
        rejectedMutationCalled = true;
      },
    ),
    /文稿清单和页面总大小超过/,
  );
  assert.equal(rejectedMutationCalled, false);
  assert.deepEqual(
    [...context.textByteSizes.entries()],
    [
      ["deck.pptd", encoder.encode(manifest).byteLength],
      ["pages/01.page", encoder.encode(pageOne).byteLength],
    ],
  );

  let acceptedPlan;
  withSavePreflight(
    payload,
    context,
    { ...baseLimits, maxDeckTextBytes: totalBytes },
    (plan) => {
      acceptedPlan = plan;
    },
  );
  assert.deepEqual(
    [...acceptedPlan.nextTextByteSizes.entries()],
    [
      ["deck.pptd", encoder.encode(nextManifest).byteLength],
      ["pages/01.page", encoder.encode(pageOne).byteLength],
      ["pages/02.page", encoder.encode(pageTwo).byteLength],
    ],
  );
});

test("save preflight enforces reload-compatible path depth before mutation", () => {
  const originalManifest = JSON.stringify({ pages: ["pages/old.page"] });
  const baseContext = {
    source: "demo",
    manifestPath: "deck.pptd",
    manifestDirectory: "",
    memoryFiles: new Map([
      ["deck.pptd", originalManifest],
      ["pages/old.page", "{}"],
    ]),
    fileIndex: new Map(),
    writablePathAliases: buildWritablePathAliases("deck.pptd", ["pages/old.page"]),
    imageReferences: new Map([
      ["deck.pptd", []],
      ["pages/old.page", []],
    ]),
    imageGrants: new Set(),
    projectEntryCount: 66,
  };
  const limits = {
    maxChangeCount: 600,
    maxTextBytes: 20 * 1024 * 1024,
    maxSaveBytes: 100 * 1024 * 1024,
  };
  const path64 = `${Array.from({ length: 64 }, (_, index) => `d${index}`).join("/")}/new.page`;
  const parent64 = path64.slice(0, path64.lastIndexOf("/"));
  const context64 = {
    ...baseContext,
    projectDirectories: new Set([parent64]),
  };
  let accepted = 0;
  withSavePreflight({
    changes: [
      {
        path: "deck.pptd",
        operate: "update",
        content: JSON.stringify({ pages: [path64] }),
      },
      { path: path64, operate: "create", content: "{}" },
    ],
  }, context64, limits, () => {
    accepted += 1;
  });
  assert.equal(accepted, 1);

  const path65 = `extra/${path64}`;
  let rejectedMutation = 0;
  assert.throws(
    () => withSavePreflight({
      changes: [
        {
          path: "deck.pptd",
          operate: "update",
          content: JSON.stringify({ pages: [path65] }),
        },
        { path: path65, operate: "create", content: "{}" },
      ],
    }, {
      ...baseContext,
      projectDirectories: new Set([path65.slice(0, path65.lastIndexOf("/"))]),
    }, limits, () => {
      rejectedMutation += 1;
    }),
    /目录深度超过 64 层/,
  );
  assert.equal(rejectedMutation, 0);
});

test("save preflight rejects new pages before exceeding the project entry budget", () => {
  const manifest = JSON.stringify({ pages: ["pages/old.page"] });
  const context = {
    source: "demo",
    manifestPath: "deck.pptd",
    manifestDirectory: "",
    memoryFiles: new Map([
      ["deck.pptd", manifest],
      ["pages/old.page", "{}"],
    ]),
    fileIndex: new Map(),
    writablePathAliases: buildWritablePathAliases("deck.pptd", ["pages/old.page"]),
    imageReferences: new Map([
      ["deck.pptd", []],
      ["pages/old.page", []],
    ]),
    imageGrants: new Set(),
    projectEntryCount: 10_000,
    projectDirectories: new Set(["pages"]),
  };
  let mutationCalled = false;
  assert.throws(
    () => withSavePreflight({
      changes: [
        {
          path: "deck.pptd",
          operate: "update",
          content: JSON.stringify({ pages: ["pages/old.page", "pages/new.page"] }),
        },
        { path: "pages/new.page", operate: "create", content: "{}" },
      ],
    }, context, {
      maxChangeCount: 600,
      maxTextBytes: 20 * 1024 * 1024,
      maxSaveBytes: 100 * 1024 * 1024,
      maxProjectEntries: 10_000,
    }, () => {
      mutationCalled = true;
    }),
    /项目条目超过 10000 个安全上限/,
  );
  assert.equal(mutationCalled, false);
});
