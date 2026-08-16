import {
  basename,
  buildImagePathAliases,
  buildWritablePathAliases,
  collectImageProjectPaths,
  dirname,
  extractReferencedImagePaths,
  extractPagePaths,
  joinManifestPath,
  normalizeRelativePath,
  titleFromManifest,
  validateNewPageBackups,
} from "./lib.js";
import {
  abortDiscardReload,
  assertDiscardReloadAuthorized,
  assertSaveQueueSwitchClean,
  authorizeDiscardReload,
  cancelSaveQueueSwitch,
  commitDiscardReload,
  enqueueSave,
  finishSaveQueueSwitch,
  freezeAndDrainSaveQueue,
  hasUncertainSaveState,
  isSaveQueueSwitchPoisoned,
  MAX_PENDING_SAVES,
  MAX_PENDING_SAVE_BYTES,
  poisonRemoteSession,
  requiresDiscardReload,
  resetSaveLifecycle,
  restoreEditingAfterSaveDrain,
  shouldWarnBeforeUnload,
} from "./save-lifecycle.js";
import {
  collectSaveAttemptPaths,
  measureSavePayloadBytes,
  sanitizeSavePayload,
  withSavePreflight,
} from "./save-plan.js";
import {
  indexProjectDirectory,
  indexUploadedProjectFiles,
  MAX_PROJECT_ENTRIES,
  projectIndexMetadata,
  projectMetadataFromPaths,
} from "./project-index.js";
import {
  assertBackupMatchesBaseline,
  deleteVersionedFile,
  readVersionedBackup,
  rollbackVersionedChanges,
  writeVersionedFile,
} from "./file-transaction.js";
import {
  assertNoReservedSaveJournalPathAliases,
  beginSaveJournal,
  finishSaveJournal,
  hasPendingSaveJournal,
  MAX_SAVE_JOURNAL_BYTES,
  orderChangesForCrashConsistency,
  recoverPendingSaveJournal,
} from "./save-journal.js";
import {
  createDirectoryProjectScopeResolver,
  createEphemeralProjectScope,
  createJournalAuthorizationStore,
  createProjectJournalLock,
} from "./journal-authorization.js";
import { decodeUtf8, readFileUtf8 } from "./text-codec.js";
import { createRemoteRpc } from "./remote-rpc.js";
import { CallOptions, connect, WindowMessenger } from "./penpal.mjs";

const EDITOR_ORIGIN = "https://www.kimi.com";
const MAX_TEXT_BYTES = 20 * 1024 * 1024;
const MAX_DECK_TEXT_BYTES = 100 * 1024 * 1024;
const MAX_SAVE_BYTES = 100 * 1024 * 1024;
const MAX_BACKUP_BYTES = 100 * 1024 * 1024;
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const MAX_IMAGE_REQUESTS = 100;
const MAX_IMAGE_RESPONSE_BYTES = 100 * 1024 * 1024;
const MAX_EXTERNAL_IMAGE_REFERENCE_BYTES = 16 * 1024;
const MAX_ENCODED_IMAGE_BYTES = Math.ceil(MAX_IMAGE_BYTES / 3) * 4 + 1024;
const MAX_CACHED_IMAGE_BYTES = 1024 * 1024;
const MAX_CACHED_IMAGE_COUNT = 50;
const MAX_CHANGE_COUNT = 600;
const MAX_PAGE_COUNT = 500;
const REMOTE_RPC_TIMEOUT_MS = 15_000;
const invokeRemote = createRemoteRpc({
  CallOptionsClass: CallOptions,
  defaultTimeoutMs: REMOTE_RPC_TIMEOUT_MS,
});
const directoryProjectScopeResolver = createDirectoryProjectScopeResolver();

function authorizationStoreForProject(projectScopeId) {
  return createJournalAuthorizationStore(
    () => window.localStorage,
    { projectScopeId },
  );
}

const elements = {
  frame: document.querySelector("#editor"),
  openFolder: document.querySelector("#open-folder"),
  openDemo: document.querySelector("#open-demo"),
  reload: document.querySelector("#reload-deck"),
  documentTitle: document.querySelector("#document-title"),
  documentPath: document.querySelector("#document-path"),
  connectionStatus: document.querySelector("#connection-status"),
  connectionLabel: document.querySelector("#connection-label"),
  saveState: document.querySelector("#save-state"),
  loadingCard: document.querySelector("#loading-card"),
  loadingTitle: document.querySelector("#loading-title"),
  loadingMessage: document.querySelector("#loading-message"),
  activityPanel: document.querySelector("#activity-panel"),
  activityList: document.querySelector("#activity-list"),
  toggleActivity: document.querySelector("#toggle-activity"),
  closeActivity: document.querySelector("#close-activity"),
  openDialog: document.querySelector("#open-dialog"),
  closeOpenDialog: document.querySelector("#close-open-dialog"),
  uploadDropzone: document.querySelector("#upload-dropzone"),
  chooseWritableFolder: document.querySelector("#choose-writable-folder"),
  uploadFolder: document.querySelector("#upload-folder"),
  deckDialog: document.querySelector("#deck-dialog"),
  deckOptions: document.querySelector("#deck-options"),
  folderFallback: document.querySelector("#folder-fallback"),
  toastRegion: document.querySelector("#toast-region"),
};

const state = {
  remote: null,
  connection: null,
  remoteEpoch: 0,
  remoteUncertainty: null,
  source: "demo",
  directoryHandle: null,
  fileIndex: new Map(),
  memoryFiles: new Map(),
  textByteSizes: new Map(),
  fileVersions: new Map(),
  projectEntryCount: 0,
  projectDirectories: new Set(),
  manifestPath: "presentation.pptd",
  manifestDirectory: "",
  manifestContent: "",
  deckTitle: "演示文稿",
  lastDeckPayload: null,
  saveQueue: Promise.resolve(),
  saveFailure: null,
  pendingSaveCount: 0,
  pendingSaveBytes: 0,
  saveBlocked: false,
  imageCache: new Map(),
  imagePathAliases: new Map(),
  imageReferences: new Map(),
  imageGrants: new Set(),
  writablePathAliases: new Map(),
  dragDepth: 0,
  readOnlyFallback: false,
  journalProjectScopeId: createEphemeralProjectScope("memory"),
  journalAuthorizationStore: null,
  journalTransactionLock: null,
  revision: 0,
  switchEpoch: 0,
  switchLease: null,
  switchPoison: null,
  discardReloadAuthorization: null,
  confirmedDiscardUnload: false,
  preparingSwitch: false,
  switching: false,
  loadingProject: null,
};

function activeProject() {
  return {
    source: state.source,
    directoryHandle: state.directoryHandle,
    fileIndex: state.fileIndex,
    memoryFiles: state.memoryFiles,
    textByteSizes: state.textByteSizes,
    fileVersions: state.fileVersions,
    projectEntryCount: state.projectEntryCount,
    projectDirectories: state.projectDirectories,
    imageCache: state.imageCache,
    imagePathAliases: state.imagePathAliases,
    imageReferences: state.imageReferences,
    imageGrants: state.imageGrants,
    writablePathAliases: state.writablePathAliases,
    readOnlyFallback: state.readOnlyFallback,
    journalProjectScopeId: state.journalProjectScopeId,
    journalAuthorizationStore: state.journalAuthorizationStore,
    journalTransactionLock: state.journalTransactionLock,
  };
}

function captureSaveContext() {
  return Object.freeze({
    ...activeProject(),
    manifestPath: state.manifestPath,
    manifestDirectory: state.manifestDirectory,
    revision: state.revision,
  });
}

function isActiveContext(context) {
  return context.revision === state.revision;
}

function createProject({
  source,
  directoryHandle = null,
  fileIndex = new Map(),
  memoryFiles = new Map(),
  textByteSizes = new Map(),
  fileVersions = new Map(),
  projectEntryCount = null,
  projectDirectories = null,
  readOnlyFallback = false,
  journalProjectScopeId = source === "demo"
    ? createEphemeralProjectScope("memory")
    : createEphemeralProjectScope("upload"),
  journalAuthorizationStore = null,
  journalTransactionLock = null,
}) {
  const metadata = projectIndexMetadata(fileIndex);
  return {
    source,
    directoryHandle,
    fileIndex,
    memoryFiles,
    textByteSizes,
    fileVersions,
    projectEntryCount: projectEntryCount ?? metadata.entryCount,
    projectDirectories: projectDirectories ?? metadata.directories,
    imageCache: new Map(),
    imagePathAliases: new Map(),
    imageReferences: new Map(),
    imageGrants: new Set(),
    writablePathAliases: new Map(),
    readOnlyFallback,
    journalProjectScopeId,
    journalAuthorizationStore,
    journalTransactionLock,
  };
}

function commitProject(project, discardAuthorization = null) {
  if (discardAuthorization) {
    assertDiscardReloadAuthorized(state, discardAuthorization);
  } else if (requiresDiscardReload(state) || state.discardReloadAuthorization) {
    throw new Error("不能在未完成确认恢复事务时提交文稿");
  }
  state.source = project.source;
  state.directoryHandle = project.directoryHandle;
  state.fileIndex = project.fileIndex;
  state.memoryFiles = project.memoryFiles;
  state.textByteSizes = project.textByteSizes;
  state.fileVersions = project.fileVersions;
  state.projectEntryCount = project.projectEntryCount;
  state.projectDirectories = project.projectDirectories;
  state.imageCache = project.imageCache;
  state.imagePathAliases = project.imagePathAliases;
  state.imageReferences = project.imageReferences;
  state.imageGrants = project.imageGrants;
  state.writablePathAliases = project.writablePathAliases;
  state.readOnlyFallback = project.readOnlyFallback;
  state.journalProjectScopeId = project.journalProjectScopeId;
  state.journalAuthorizationStore = project.journalAuthorizationStore;
  state.journalTransactionLock = project.journalTransactionLock;
  if (discardAuthorization) commitDiscardReload(state, discardAuthorization);
  else resetSaveLifecycle(state);
  state.revision += 1;
}

function setConnection(kind, label) {
  elements.connectionStatus.className = `status-pill status-${kind}`;
  elements.connectionLabel.textContent = label;
  document.documentElement.dataset.connection = kind;
}

function setSaveState(label, kind = "idle") {
  elements.saveState.textContent = label;
  elements.saveState.dataset.kind = kind;
  document.documentElement.dataset.saveState = kind;
}

function setLoading(visible, title, message) {
  elements.loadingCard.classList.toggle("is-hidden", !visible);
  elements.loadingCard.setAttribute("aria-hidden", String(!visible));
  if (title) elements.loadingTitle.textContent = title;
  if (message) elements.loadingMessage.textContent = message;
}

function formatTime(date = new Date()) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function addActivity(message, tone = "info") {
  const item = document.createElement("li");
  item.dataset.tone = tone;
  item.innerHTML = `<time>${formatTime()}</time><span></span>`;
  item.querySelector("span").textContent = message;
  elements.activityList.prepend(item);
  while (elements.activityList.children.length > 40) {
    elements.activityList.lastElementChild.remove();
  }
}

function toast(message, tone = "info") {
  const item = document.createElement("div");
  item.className = `toast toast-${tone}`;
  item.textContent = message;
  elements.toastRegion.append(item);
  requestAnimationFrame(() => item.classList.add("is-visible"));
  setTimeout(() => {
    item.classList.remove("is-visible");
    setTimeout(() => item.remove(), 220);
  }, 3200);
}

function editorQuery() {
  return new URLSearchParams({
    sdkMode: "ppt-editor",
    pptPlatform: "neodeck-local",
    functional: JSON.stringify({
      fullscreen: true,
      present: true,
      export: true,
      close: false,
      annotation: false,
      feedback: false,
      share: false,
      versionHistory: false,
    }),
    sdkSaveMode: "external",
    sdkImageMode: "external",
  });
}

function demoDeck() {
  const manifest = JSON.stringify({
    version: "v2",
    title: "NeoDeck Local",
    size: [960, 540],
    pages: ["pages/01.page", "pages/02.page"],
  });
  const pages = [
    {
      path: "pages/01.page",
      content: JSON.stringify({
        pageType: "content",
        background: { color: "#f7f8fc" },
        elements: [
          {
            elementId: "eyebrow",
            elementType: "text",
            bounds: [92, 96, 776, 40],
            content: {
              text: '<p><span style="font-size:16px;color:#6c5ce7;font-weight:700;letter-spacing:2px">NEODECK LOCAL</span></p>',
            },
          },
          {
            elementId: "title",
            elementType: "text",
            bounds: [92, 155, 776, 142],
            content: {
              text: '<p><span style="font-size:52px;color:#171923;font-weight:700">直接打开和保存<br/>本地 PPTD</span></p>',
            },
          },
          {
            elementId: "subtitle",
            elementType: "text",
            bounds: [94, 330, 660, 58],
            content: {
              text: '<p><span style="font-size:20px;color:#667085">第三方宿主 · 本地文件 · Kimi neo-ppt 编辑内核</span></p>',
            },
          },
        ],
        animations: [
          {
            elementId: "title",
            effect: "fade-in",
            trigger: "onClick",
            direction: "up",
            easing: "ease-in-out",
            durationMs: 700,
          },
        ],
      }),
    },
    {
      path: "pages/02.page",
      content: JSON.stringify({
        pageType: "content",
        background: { color: "#171923" },
        elements: [
          {
            elementId: "second-title",
            elementType: "text",
            bounds: [100, 120, 760, 96],
            content: {
              text: '<p><span style="font-size:42px;color:#ffffff;font-weight:700">文件系统桥接已经就绪</span></p>',
            },
          },
          {
            elementId: "steps",
            elementType: "text",
            bounds: [102, 250, 730, 165],
            content: {
              text: '<p><span style="font-size:22px;color:#cbd5e1">① 选择完整的 PPTD 项目文件夹</span></p><p><span style="font-size:22px;color:#cbd5e1">② 编辑器自动读取清单、页面与素材</span></p><p><span style="font-size:22px;color:#cbd5e1">③ 自动保存变更到原目录</span></p>',
            },
          },
        ],
      }),
    },
  ];
  return {
    id: "neodeck-demo",
    title: "NeoDeck Local",
    manifestPath: "presentation.pptd",
    manifestContent: manifest,
    pages,
    basePath: "",
    isCreate: true,
  };
}

function requireRemote() {
  if (!state.remote) throw new Error("编辑器还没有连接完成");
  return state.remote;
}

function invalidateRemoteSession(remote, remoteEpoch, error) {
  if (remote !== state.remote || remoteEpoch !== state.remoteEpoch) return false;
  poisonRemoteSession(state, error, state.switchLease);
  const connection = state.connection;
  state.connection = null;
  state.remote = null;
  state.remoteEpoch += 1;
  connection?.destroy();
  // Destroy the old JavaScript realm, not just its local Penpal proxy. A
  // timed-out mutation may still be executing inside that iframe.
  elements.frame.removeAttribute("src");
  elements.reload.disabled = !state.lastDeckPayload;
  setConnection("error", "编辑器会话已失效");
  setSaveState("远端结果不确定，请重新载入", "error");
  return true;
}

function invokeCurrentRemote(remote, method, args = [], options = {}) {
  const remoteEpoch = state.remoteEpoch;
  const callerSessionCheck = options.isSessionCurrent;
  return invokeRemote(remote, method, args, {
    ...options,
    isSessionCurrent: () => (
      remote === state.remote
      && remoteEpoch === state.remoteEpoch
      && (callerSessionCheck ? callerSessionCheck() : true)
    ),
  }).catch((error) => {
    if (error?.remoteMayHaveApplied || error?.remoteSessionUnusable) {
      invalidateRemoteSession(remote, remoteEpoch, error);
    }
    throw error;
  });
}

async function beginDeckSwitch(discardAuthorization = null) {
  const remote = requireRemote();
  const remoteEpoch = state.remoteEpoch;
  return freezeAndDrainSaveQueue(
    state,
    remote,
    {
      discardAuthorization,
      invokeRemote: invokeCurrentRemote,
      rpcTimeoutMs: REMOTE_RPC_TIMEOUT_MS,
      onRemoteUncertain(error) {
        invalidateRemoteSession(remote, remoteEpoch, error);
      },
    },
  );
}

async function cancelDeckSwitch(lease) {
  const remote = state.remote;
  const remoteEpoch = state.remoteEpoch;
  await cancelSaveQueueSwitch(state, remote, {
    lease,
    invokeRemote: invokeCurrentRemote,
    rpcTimeoutMs: REMOTE_RPC_TIMEOUT_MS,
    onRemoteUncertain(error) {
      invalidateRemoteSession(remote, remoteEpoch, error);
    },
    onRestoreError(error) {
      addActivity(`恢复编辑状态失败：${error.message}`, "error");
    },
  });
}

async function setDeck(
  payload,
  sourceLabel,
  project = activeProject(),
  {
    switchLease: preparedLease = null,
    discardAuthorization = null,
  } = {},
) {
  const remote = requireRemote();
  const switchLease = preparedLease ?? await beginDeckSwitch(discardAuthorization);
  assertSaveQueueSwitchClean(state, switchLease);
  const previousPayload = state.lastDeckPayload;
  const previousProject = activeProject();
  let committed = false;
  let setPPTDStarted = false;
  try {
    setLoading(true, "正在载入 PPTD", sourceLabel);
    state.loadingProject = Object.freeze({
      ...project,
      manifestDirectory: dirname(payload.manifestPath),
    });
    // Check both sides of the remote commit. A late save while setPPTD is in
    // flight taints this lease, because the iframe may already have applied the
    // new deck even if the RPC later rejects.
    assertSaveQueueSwitchClean(state, switchLease);
    setPPTDStarted = true;
    await invokeCurrentRemote(remote, "setPPTD", [
      payload.id,
      {
        pptdContent: payload.manifestContent,
        pages: payload.pages,
        basePath: payload.basePath,
        pptdPath: payload.manifestPath,
        isCreate: payload.isCreate ?? true,
      },
    ]);
    assertSaveQueueSwitchClean(state, switchLease);

    // setPPTD is the commit point: until it succeeds, the old project and all
    // of its file capabilities remain untouched.
    commitProject(project, switchLease.discardAuthorization);
    committed = true;
    state.lastDeckPayload = payload;
    state.manifestContent = payload.manifestContent;
    state.manifestPath = payload.manifestPath;
    state.manifestDirectory = dirname(payload.manifestPath);
    state.deckTitle = payload.title;
    elements.documentTitle.textContent = payload.title;
    elements.documentPath.textContent = sourceLabel;
    elements.reload.disabled = false;

    await restoreEditingAfterSaveDrain(
      state,
      remote,
      !project.readOnlyFallback,
      switchLease,
    );
    let status = null;
    try {
      status = await invokeCurrentRemote(
        remote,
        "getSlideStatus",
        [],
        { mayHaveApplied: false },
      );
    } catch (statusError) {
      if (statusError?.remoteSessionUnusable) throw statusError;
      addActivity(`文稿已载入，但状态查询失败：${statusError.message}`, "warning");
    }
    setLoading(false);
    if (project.source === "demo") setSaveState("示例 · 内存保存", "memory");
    else if (project.readOnlyFallback) setSaveState("只读打开", "readonly");
    else setSaveState("自动保存已开启", "saved");
    addActivity(`已载入「${payload.title}」，共 ${payload.pages.length} 页`, "success");
    document.documentElement.dataset.deckStatus = "ready";
    window.dispatchEvent(new CustomEvent("neodeck:ready", { detail: status }));
  } catch (error) {
    const poisoned = isSaveQueueSwitchPoisoned(state, switchLease);
    if (
      !committed
      && previousPayload
      && setPPTDStarted
      && !error?.remoteMayHaveApplied
      && state.remote === remote
    ) {
      try {
        // The first remote commit completed and a later local lease check
        // failed (for example, a late-save poison). In that narrow case the
        // session is still certain and reinstalling the old deck is ordered.
        // Uncertain RPC failures invalidate the whole iframe earlier and must
        // never enter this same-session compensation path.
        state.loadingProject = Object.freeze({
          ...previousProject,
          manifestDirectory: dirname(previousPayload.manifestPath),
        });
        await invokeCurrentRemote(remote, "setPPTD", [
          previousPayload.id,
          {
            pptdContent: previousPayload.manifestContent,
            pages: previousPayload.pages,
            basePath: previousPayload.basePath,
            pptdPath: previousPayload.manifestPath,
            isCreate: previousPayload.isCreate ?? true,
          },
        ]);
        if (!poisoned && !state.saveBlocked) {
          await restoreEditingAfterSaveDrain(
            state,
            remote,
            !previousProject.readOnlyFallback,
            switchLease,
          );
        } else {
          setConnection("error", "切换已冻结");
          setSaveState("存在未确认保存，请重新载入", "error");
        }
      } catch (recoveryError) {
        state.saveBlocked = true;
        setConnection("error", "编辑器状态不确定");
        setSaveState("编辑器已冻结，请刷新恢复", "error");
        throw new AggregateError(
          [error, recoveryError],
          "切换文稿失败，且无法确认已恢复旧文稿；编辑器已保持冻结",
        );
      }
    } else if (committed && !poisoned && !state.saveBlocked) {
      await restoreEditingAfterSaveDrain(
        state,
        remote,
        !project.readOnlyFallback,
        switchLease,
      ).catch(() => undefined);
    } else if (!poisoned) {
      state.saveBlocked = true;
      elements.reload.disabled = false;
      setConnection("error", "编辑器载入失败");
      setSaveState("编辑器已冻结，请重试", "error");
    } else {
      elements.reload.disabled = false;
      setConnection("error", "切换已冻结");
      setSaveState("存在未确认保存，请重新载入", "error");
    }
    throw error;
  } finally {
    state.loadingProject = null;
    finishSaveQueueSwitch(state, switchLease);
  }
}

async function openDemo() {
  const payload = demoDeck();
  const project = createProject({ source: "demo" });
  project.writablePathAliases = buildWritablePathAliases(
    payload.manifestPath,
    payload.pages.map((page) => page.path),
  );
  project.memoryFiles.set(payload.manifestPath, payload.manifestContent);
  project.textByteSizes.set(
    payload.manifestPath,
    new TextEncoder().encode(payload.manifestContent).byteLength,
  );
  for (const page of payload.pages) {
    project.memoryFiles.set(page.path, page.content);
    project.textByteSizes.set(page.path, new TextEncoder().encode(page.content).byteLength);
  }
  const metadata = projectMetadataFromPaths(project.memoryFiles.keys());
  project.projectEntryCount = metadata.entryCount;
  project.projectDirectories = metadata.directories;
  await setDeck(payload, "内置示例 · 修改保存在本页内存", project);
}

async function chooseManifest(paths) {
  if (paths.length === 1) return paths[0];
  elements.deckOptions.replaceChildren();
  const choice = new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    for (const path of paths) {
      const button = document.createElement("button");
      button.className = "deck-option";
      button.type = "button";
      button.innerHTML = `<span class="deck-file-icon">P</span><span><strong></strong><small></small></span><span>›</span>`;
      button.querySelector("strong").textContent = basename(path);
      button.querySelector("small").textContent = path;
      button.addEventListener("click", () => {
        finish(path);
        elements.deckDialog.close();
      });
      elements.deckOptions.append(button);
    }
    elements.deckDialog.addEventListener(
      "close",
      () => finish(null),
      { once: true },
    );
  });
  elements.deckDialog.showModal();
  return choice;
}

async function textFromIndexedFile(path, project = activeProject()) {
  const normalizedPath = normalizeRelativePath(path);
  if (project.source === "directory" && project.directoryHandle) {
    const snapshot = await readVersionedBackup(
      project,
      normalizedPath,
      { maxBytes: MAX_TEXT_BYTES },
    );
    if (!snapshot.existed) throw new Error(`找不到文件：${path}`);
    const text = decodeUtf8(snapshot.bytes, normalizedPath);
    project.fileVersions.set(normalizedPath, snapshot.version);
    return { text, byteLength: snapshot.bytes.byteLength };
  }
  const entry = project.fileIndex.get(normalizedPath);
  if (!entry) throw new Error(`找不到文件：${path}`);
  const file = await entry.getFile();
  return readFileUtf8(file, normalizedPath, { maxBytes: MAX_TEXT_BYTES });
}

async function loadManifest(
  manifestPath,
  sourceLabel,
  project = activeProject(),
  {
    switchLease: preparedLease = null,
    discardAuthorization = null,
  } = {},
) {
  const switchLease = preparedLease ?? await beginDeckSwitch(discardAuthorization);
  try {
    const manifestFile = await textFromIndexedFile(manifestPath, project);
    const manifestContent = manifestFile.text;
    const pagePaths = extractPagePaths(manifestContent, { maxPageCount: MAX_PAGE_COUNT });
    const manifestDirectory = dirname(manifestPath);
    const pages = [];
    const textByteSizes = new Map([[manifestPath, manifestFile.byteLength]]);
    let totalBytes = manifestFile.byteLength;
    const imageReferences = new Map([
      [manifestPath, extractReferencedImagePaths(manifestContent)],
    ]);
    for (const pagePath of pagePaths) {
      const indexedPath = joinManifestPath(manifestDirectory, pagePath);
      const pageFile = await textFromIndexedFile(indexedPath, project);
      const content = pageFile.text;
      totalBytes += pageFile.byteLength;
      if (totalBytes > MAX_DECK_TEXT_BYTES) {
        throw new Error("文稿清单和页面总大小超过 100 MiB 安全上限");
      }
      pages.push({ path: pagePath, content });
      textByteSizes.set(indexedPath, pageFile.byteLength);
      imageReferences.set(indexedPath, extractReferencedImagePaths(content));
    }
    project.imageReferences = imageReferences;
    project.textByteSizes = textByteSizes;
    const referencedImages = [...imageReferences.values()].flat();
    project.imageGrants = collectImageProjectPaths(manifestPath, referencedImages);
    project.imagePathAliases = buildImagePathAliases(
      manifestPath,
      referencedImages,
    );
    project.imageCache = new Map();
    project.writablePathAliases = buildWritablePathAliases(manifestPath, pagePaths);

    const fallbackTitle = basename(manifestPath).replace(/\.pptd$/i, "");
    const title = titleFromManifest(manifestContent, fallbackTitle);
    await setDeck(
      {
        id: fallbackTitle,
        title,
        manifestPath,
        manifestContent,
        pages,
        basePath: manifestDirectory ? `${manifestDirectory}/` : "",
        isCreate: true,
      },
      sourceLabel,
      project,
      { switchLease },
    );
  } catch (error) {
    await cancelDeckSwitch(switchLease);
    throw error;
  }
}

async function recoverProjectSaveIfNeeded(
  directoryHandle,
  label,
  authorizationStore,
  transactionLock,
) {
  if (!transactionLock || typeof transactionLock.runExclusive !== "function") {
    throw new Error("当前目录缺少跨标签页保存事务锁；已拒绝恢复或修改项目");
  }
  const result = await transactionLock.runExclusive(() => recoverPendingSaveJournal(
    directoryHandle,
    {
      maxJournalBytes: MAX_SAVE_JOURNAL_BYTES,
      maxFileBytes: MAX_TEXT_BYTES,
      maxBackupBytes: MAX_BACKUP_BYTES,
      authorizationStore,
    },
  ));
  if (result.recovered) {
    addActivity(`已恢复「${label}」中上次中断的多文件保存`, "warning");
    toast("检测到上次中断的保存，已恢复完整旧版本", "warning");
  }
  if (result.cleanedPreparation) {
    addActivity(`已清理「${label}」中尚未进入文稿修改阶段的残留恢复日志`, "warning");
  }
  if (result.cleanedCommitted) {
    addActivity(`已完成「${label}」中已提交保存的恢复日志收口`, "warning");
  }
  return result;
}

async function assertNoPendingReadOnlyRecovery(directoryHandle) {
  if (await hasPendingSaveJournal(directoryHandle)) {
    throw new Error("项目包含未完成的保存恢复日志；请使用可写文件夹授权完成恢复后再打开");
  }
}

async function openDirectoryHandle(directoryHandle, label = directoryHandle.name) {
  setLoading(true, "正在扫描文件夹", label);
  let permission = await directoryHandle.queryPermission?.({ mode: "readwrite" });
  if (permission !== "granted") permission = await directoryHandle.requestPermission?.({ mode: "readwrite" });
  if (permission !== "granted") throw new Error("没有获得文件夹读写权限");

  const journalProjectScopeId = await directoryProjectScopeResolver.resolve(directoryHandle);
  const journalAuthorizationStore = authorizationStoreForProject(journalProjectScopeId);
  const journalTransactionLock = createProjectJournalLock(journalProjectScopeId);
  await recoverProjectSaveIfNeeded(
    directoryHandle,
    label,
    journalAuthorizationStore,
    journalTransactionLock,
  );

  const fileIndex = await indexProjectDirectory(directoryHandle);
  assertNoReservedSaveJournalPathAliases(fileIndex.keys());
  const project = createProject({
    source: "directory",
    directoryHandle,
    fileIndex,
    journalProjectScopeId,
    journalAuthorizationStore,
    journalTransactionLock,
  });
  const manifests = [...project.fileIndex.keys()].filter((path) => path.toLowerCase().endsWith(".pptd"));
  if (manifests.length === 0) throw new Error("文件夹里没有找到 .pptd 清单文件");
  const manifestPath = await chooseManifest(manifests.sort());
  if (!manifestPath) {
    setLoading(false);
    return;
  }
  await loadManifest(manifestPath, `${label}/${manifestPath}`, project);
}

async function openFallbackFiles(files) {
  const fileIndex = indexUploadedProjectFiles(files);
  assertNoReservedSaveJournalPathAliases(fileIndex.keys());
  const project = createProject({
    source: "fallback",
    fileIndex,
    readOnlyFallback: true,
  });
  const manifests = [...project.fileIndex.keys()].filter((path) => path.toLowerCase().endsWith(".pptd"));
  if (manifests.length === 0) throw new Error("文件夹里没有找到 .pptd 清单文件");
  const manifestPath = await chooseManifest(manifests.sort());
  if (manifestPath) await loadManifest(manifestPath, `${manifestPath} · 只读兼容模式`, project);
}

async function openDirectoryHandleReadOnly(directoryHandle, label = directoryHandle.name) {
  setLoading(true, "正在扫描文件夹", label);
  await assertNoPendingReadOnlyRecovery(directoryHandle);
  const fileIndex = await indexProjectDirectory(directoryHandle);
  assertNoReservedSaveJournalPathAliases(fileIndex.keys());
  const project = createProject({
    source: "fallback",
    fileIndex,
    readOnlyFallback: true,
  });
  const manifests = [...project.fileIndex.keys()].filter((path) => path.toLowerCase().endsWith(".pptd"));
  if (manifests.length === 0) throw new Error("文件夹里没有找到 .pptd 清单文件");
  const manifestPath = await chooseManifest(manifests.sort());
  if (manifestPath) await loadManifest(manifestPath, `${label}/${manifestPath} · 拖放只读模式`, project);
}

async function pickDirectory() {
  try {
    if ("showDirectoryPicker" in window) {
      const directoryHandle = await window.showDirectoryPicker({ id: "neodeck-project", mode: "readwrite" });
      setOpenDialog(false);
      await openDirectoryHandle(directoryHandle);
    } else {
      elements.folderFallback.click();
    }
  } catch (error) {
    if (error?.name === "AbortError") return;
    handleError(error, "打开文件夹失败");
  }
}

async function openDroppedFolder(handles) {
  const usableHandles = handles.filter(Boolean);
  if (usableHandles.length !== 1 || usableHandles[0].kind !== "directory") {
    throw new Error("只能拖入一个完整的 PPTD 项目文件夹，不能单独拖入 .pptd 文件");
  }

  const directoryHandle = usableHandles[0];
  try {
    setOpenDialog(false);
    await openDirectoryHandle(directoryHandle);
  } catch (error) {
    setOpenDialog(false);
    toast("无法以可写模式载入，正在尝试只读打开", "warning");
    await openDirectoryHandleReadOnly(directoryHandle);
  }
}

function resolveIndexedPath(requestedPath, context) {
  if (typeof requestedPath !== "string" || !requestedPath.trim()) return null;
  if (requestedPath.length > 4096) return null;
  if (/^(?:data:image\/|https?:\/\/|blob:)/i.test(requestedPath)) return requestedPath;
  let candidate;
  try {
    candidate = normalizeRelativePath(requestedPath);
  } catch {
    return null;
  }

  const canonical = context.imagePathAliases.get(candidate);
  return canonical && context.fileIndex.has(canonical) ? canonical : null;
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result)));
    reader.addEventListener("error", () => reject(reader.error || new Error("图片读取失败")));
    reader.readAsDataURL(file);
  });
}

async function resolveImage(requestedPath, context, budget) {
  if (typeof requestedPath !== "string") return "";
  if (/^data:image\//i.test(requestedPath)) {
    const encodedBytes = new TextEncoder().encode(requestedPath).byteLength;
    if (encodedBytes > MAX_ENCODED_IMAGE_BYTES) return "";
    if (budget.bytes + encodedBytes > MAX_IMAGE_RESPONSE_BYTES) {
      throw new Error("单次图片响应总大小超过 100 MiB 安全上限");
    }
    budget.bytes += encodedBytes;
    return requestedPath;
  }
  if (/^(?:https?:\/\/|blob:)/i.test(requestedPath)) {
    const referenceBytes = new TextEncoder().encode(requestedPath).byteLength;
    if (referenceBytes > MAX_EXTERNAL_IMAGE_REFERENCE_BYTES) return "";
    if (budget.bytes + referenceBytes > MAX_IMAGE_RESPONSE_BYTES) {
      throw new Error("单次图片响应总大小超过 100 MiB 安全上限");
    }
    budget.bytes += referenceBytes;
    return requestedPath;
  }
  const path = resolveIndexedPath(requestedPath, context);
  if (!path) return "";
  const entry = context.fileIndex.get(path);
  const file = await entry.getFile();
  if (file.size > MAX_IMAGE_BYTES) return "";
  if (file.type && !file.type.startsWith("image/")) return "";
  const encodedBytes = Math.ceil(file.size / 3) * 4 + 128;
  if (budget.bytes + encodedBytes > MAX_IMAGE_RESPONSE_BYTES) {
    throw new Error("单次图片响应总大小超过 100 MiB 安全上限");
  }
  budget.bytes += encodedBytes;
  const cacheKey = `${path}:${file.lastModified}:${file.size}`;
  if (file.size > MAX_CACHED_IMAGE_BYTES || context.imageCache.size >= MAX_CACHED_IMAGE_COUNT) {
    return fileToDataUrl(file);
  }
  if (!context.imageCache.has(cacheKey)) context.imageCache.set(cacheKey, fileToDataUrl(file));
  return context.imageCache.get(cacheKey);
}

async function getImages(payload = {}) {
  const paths = Array.isArray(payload.filePath) ? payload.filePath : [];
  if (paths.length > MAX_IMAGE_REQUESTS) {
    throw new Error(`单次最多读取 ${MAX_IMAGE_REQUESTS} 张图片`);
  }
  const context = state.loadingProject ?? captureSaveContext();
  const budget = { bytes: 0 };
  const result = [];
  for (const path of paths) {
    try {
      result.push(await resolveImage(path, context, budget));
    } catch (error) {
      if (/总大小超过/.test(error.message)) throw error;
      result.push("");
    }
  }
  const misses = result.filter((value) => !value).length;
  if (misses) addActivity(`${paths.length} 张图片中有 ${misses} 张未找到`, "warning");
  return result;
}

function commitImageCapabilities(context, nextCapabilities) {
  context.imageReferences.clear();
  for (const [path, references] of nextCapabilities.imageReferences) {
    context.imageReferences.set(path, references);
  }
  context.imagePathAliases.clear();
  for (const [alias, canonical] of nextCapabilities.imagePathAliases) {
    context.imagePathAliases.set(alias, canonical);
  }
  context.imageCache.clear();
}

function commitWritableAliases(context, nextAliases) {
  if (nextAliases === context.writablePathAliases) return;
  context.writablePathAliases.clear();
  for (const [alias, canonical] of nextAliases) {
    context.writablePathAliases.set(alias, canonical);
  }
}

function commitTextByteSizes(context, nextTextByteSizes) {
  if (!nextTextByteSizes) return;
  context.textByteSizes.clear();
  for (const [path, byteLength] of nextTextByteSizes) {
    context.textByteSizes.set(path, byteLength);
  }
}

function commitProjectEntryCount(context, nextProjectEntryCount) {
  if (Number.isSafeInteger(nextProjectEntryCount) && isActiveContext(context)) {
    state.projectEntryCount = nextProjectEntryCount;
  }
}

async function assertProjectEntryCount(
  context,
  expectedCount,
  phase,
  { temporaryEntries = 0 } = {},
) {
  const freshIndex = await indexProjectDirectory(context.directoryHandle, {
    maxEntries: MAX_PROJECT_ENTRIES + temporaryEntries,
  });
  const metadata = projectIndexMetadata(freshIndex);
  if (metadata.entryCount !== expectedCount) {
    throw new Error(
      `${phase}检测到项目目录结构已被外部修改（预期 ${expectedCount} 项，实际 ${metadata.entryCount} 项）；已取消保存，请重新载入`,
    );
  }
}

function applyRestoredFileVersions(context, restoredVersions) {
  if (!restoredVersions) return;
  for (const [path, version] of restoredVersions) {
    if (version?.kind === "missing") context.fileVersions.delete(path);
    else context.fileVersions.set(path, version);
  }
}

function commitFileVersions(context, nextAliases, applied) {
  const declaredPaths = new Set([context.manifestPath]);
  for (const path of nextAliases.values()) {
    if (path.toLowerCase().endsWith(".page")) declaredPaths.add(path);
  }
  const nextVersions = new Map();
  for (const path of declaredPaths) {
    const version = applied.get(path)?.writtenVersion ?? context.fileVersions.get(path);
    if (version?.kind === "file") nextVersions.set(path, version);
  }
  context.fileVersions.clear();
  for (const [path, version] of nextVersions) context.fileVersions.set(path, version);
}

function updateActivePayload(context, changes) {
  if (!isActiveContext(context) || !state.lastDeckPayload) return;
  let manifestUpdated = false;
  for (const change of changes) {
    if (change.path === state.manifestPath && change.operation === "put") {
      state.manifestContent = change.content;
      state.lastDeckPayload.manifestContent = change.content;
      manifestUpdated = true;
      continue;
    }
    const relativePath = state.manifestDirectory && change.path.startsWith(`${state.manifestDirectory}/`)
      ? change.path.slice(state.manifestDirectory.length + 1)
      : change.path;
    const page = state.lastDeckPayload.pages.find((entry) => entry.path === relativePath);
    if (!page) continue;
    if (change.operation === "delete") {
      state.lastDeckPayload.pages = state.lastDeckPayload.pages.filter((entry) => entry !== page);
    } else page.content = change.content;
  }

  if (manifestUpdated) {
    const pagePaths = extractPagePaths(
      state.manifestContent,
      { maxPageCount: MAX_PAGE_COUNT },
    );
    const aliases = buildWritablePathAliases(state.manifestPath, pagePaths);
    const changesByPath = new Map(changes.map((change) => [change.path, change]));
    const previousPages = new Map(
      state.lastDeckPayload.pages.map((page) => [normalizeRelativePath(page.path), page]),
    );
    state.lastDeckPayload.pages = pagePaths.flatMap((pagePath) => {
      const canonical = aliases.get(normalizeRelativePath(pagePath));
      const change = changesByPath.get(canonical);
      if (change?.operation === "put") return [{ path: pagePath, content: change.content }];
      const previous = previousPages.get(normalizeRelativePath(pagePath));
      return previous ? [{ path: pagePath, content: previous.content }] : [];
    });
    state.deckTitle = titleFromManifest(state.manifestContent, state.deckTitle);
    state.lastDeckPayload.title = state.deckTitle;
    elements.documentTitle.textContent = state.deckTitle;
  }
}

async function persistChanges(payload = {}, context) {
  if (context?.source === "directory") {
    if (!context.journalTransactionLock) {
      throw new Error("当前目录缺少跨标签页保存事务锁；已拒绝修改项目");
    }
    return context.journalTransactionLock.runExclusive(
      () => persistChangesUnlocked(payload, context),
    );
  }
  return persistChangesUnlocked(payload, context);
}

async function persistChangesUnlocked(payload = {}, context) {
  return withSavePreflight(payload, context, {
    maxChangeCount: MAX_CHANGE_COUNT,
    maxTextBytes: MAX_TEXT_BYTES,
    maxSaveBytes: MAX_SAVE_BYTES,
    maxDeckTextBytes: MAX_DECK_TEXT_BYTES,
    maxPageCount: MAX_PAGE_COUNT,
  }, async ({
    normalized,
    newPagePaths,
    nextAliases,
    nextImageCapabilities,
    nextTextByteSizes,
    nextProjectEntryCount,
  }) => {
    if (normalized.length === 0) {
      return { fileContent: payload.fileContent, lastModifiedTime: Date.now() };
    }

    if (context.source === "demo") {
      for (const change of normalized) {
        if (change.operation === "delete") context.memoryFiles.delete(change.path);
        else context.memoryFiles.set(change.path, change.content);
      }
      commitWritableAliases(context, nextAliases);
      commitImageCapabilities(context, nextImageCapabilities);
      commitTextByteSizes(context, nextTextByteSizes);
      commitProjectEntryCount(context, nextProjectEntryCount);
      if (isActiveContext(context)) {
        updateActivePayload(context, normalized);
        setSaveState("刚刚保存到内存", "memory");
        addActivity(`已在内存保存 ${normalized.length} 项变更`, "success");
      }
      return { fileContent: payload.fileContent, lastModifiedTime: Date.now() };
    }

    if (context.readOnlyFallback || !context.directoryHandle) {
      throw new Error("当前是只读兼容模式，请用支持文件夹授权的 Chromium 浏览器打开");
    }

    const permission = await context.directoryHandle.queryPermission({ mode: "readwrite" });
    if (permission !== "granted") throw new Error("项目文件夹的写入权限已经失效");

    await assertProjectEntryCount(context, context.projectEntryCount, "保存前");

    if (isActiveContext(context)) setSaveState("正在保存…", "saving");
    const backups = new Map();
    const applied = new Map();
    let journal = null;
    try {
      let backupBytes = 0;
      const newPages = new Set(newPagePaths);
      if (!normalized.some((change) => change.path === context.manifestPath)) {
        const manifestSnapshot = await readVersionedBackup(
          context,
          context.manifestPath,
          { maxBytes: MAX_TEXT_BYTES },
        );
        assertBackupMatchesBaseline(
          context.manifestPath,
          manifestSnapshot,
          context.fileVersions.get(context.manifestPath),
        );
      }
      for (const change of normalized) {
        const backup = await readVersionedBackup(
          context,
          change.path,
          { maxBytes: MAX_TEXT_BYTES },
        );
        backupBytes += backup.bytes?.byteLength ?? 0;
        if (backupBytes > MAX_BACKUP_BYTES) {
          throw new Error("单次保存需要的回滚备份超过 100 MiB 安全上限");
        }
        const baseline = context.fileVersions.get(change.path);
        if (baseline) {
          assertBackupMatchesBaseline(change.path, backup, baseline);
        } else if (!newPages.has(change.path)) {
          assertBackupMatchesBaseline(change.path, backup, null);
        }
        backups.set(change.path, backup);
      }
      // The in-memory index cannot model case-insensitive/Unicode-equivalent
      // filesystem aliases. The real handle lookup above is authoritative and
      // must prove that every newly authorized page is genuinely new.
      validateNewPageBackups(newPagePaths, backups);
      const orderedChanges = orderChangesForCrashConsistency(
        normalized,
        context.manifestPath,
      );
      journal = await beginSaveJournal(context, orderedChanges, backups, {
        manifestPath: context.manifestPath,
        maxBytes: MAX_SAVE_JOURNAL_BYTES,
        maxBackupBytes: MAX_BACKUP_BYTES,
        authorizationStore: context.journalAuthorizationStore,
      });
      for (const change of orderedChanges) {
        const backup = backups.get(change.path);
        const writtenVersion = change.operation === "delete"
          ? await deleteVersionedFile(
            context,
            change.path,
            backup.version,
            { maxBytes: MAX_TEXT_BYTES },
          )
          : await writeVersionedFile(
            context,
            change.path,
            change.content,
            backup.version,
            { maxBytes: MAX_TEXT_BYTES },
          );
        applied.set(change.path, { backup, writtenVersion });
      }
      // Keep the durable recovery journal until every deck mutation and the
      // directory-entry invariant have been confirmed. It is the only
      // temporary entry allowed above the normal project ceiling.
      await assertProjectEntryCount(
        context,
        nextProjectEntryCount + 1,
        "保存后（含恢复日志）",
        { temporaryEntries: 1 },
      );
      await finishSaveJournal(context, journal, {
        maxBytes: MAX_SAVE_JOURNAL_BYTES,
        authorizationStore: context.journalAuthorizationStore,
      });
      journal = null;
    } catch (error) {
      if (error?.journalFinalizationStarted) {
        // Finalization begins only after the on-disk deck is one verified
        // complete version. Never start a second rollback transaction because
        // the host commit/readback or journal cleanup was inconclusive.
        throw error;
      }
      let rollbackError = null;
      try {
        const restoredVersions = await rollbackVersionedChanges(
          context,
          applied,
          { maxBytes: MAX_TEXT_BYTES },
        );
        applyRestoredFileVersions(context, restoredVersions);
      } catch (caughtRollbackError) {
        rollbackError = caughtRollbackError;
        applyRestoredFileVersions(context, caughtRollbackError.restoredVersions);
        addActivity(`回滚未完全成功：${caughtRollbackError.message}`, "error");
      }

      let journalCleanupError = null;
      if (!rollbackError && journal && !error?.mayHaveApplied) {
        try {
          await finishSaveJournal(context, journal, {
            maxBytes: MAX_SAVE_JOURNAL_BYTES,
            authorizationStore: context.journalAuthorizationStore,
          });
          journal = null;
        } catch (cleanupError) {
          journalCleanupError = cleanupError;
        }
      }

      if (rollbackError || journalCleanupError) {
        const failures = [error, rollbackError, journalCleanupError].filter(Boolean);
        const detail = rollbackError?.message ?? journalCleanupError?.message;
        const aggregate = new AggregateError(
          failures,
          `保存失败且恢复事务未完整收口；磁盘可能处于待恢复状态：${detail}`,
        );
        aggregate.saveScopeUncertain = true;
        throw aggregate;
      }
      throw error;
    }

    commitFileVersions(context, nextAliases, applied);
    commitWritableAliases(context, nextAliases);
    commitImageCapabilities(context, nextImageCapabilities);
    commitTextByteSizes(context, nextTextByteSizes);
    commitProjectEntryCount(context, nextProjectEntryCount);
    updateActivePayload(context, normalized);
    if (isActiveContext(context)) {
      setSaveState("刚刚已保存", "saved");
      addActivity(`已保存 ${normalized.length} 个文件变更`, "success");
    }
    return { fileContent: payload.fileContent, lastModifiedTime: Date.now() };
  });
}

function onSave(payload) {
  return enqueueSave(state, payload, {
    captureContext: captureSaveContext,
    getAttempt: (savePayload, context) => collectSaveAttemptPaths(
      savePayload,
      context,
      {
        maxChangeCount: MAX_CHANGE_COUNT,
        maxTextBytes: MAX_TEXT_BYTES,
        maxPageCount: MAX_PAGE_COUNT,
      },
    ),
    isActiveContext,
    persistChanges,
    onStickyFailure() {
      if (hasUncertainSaveState(state)) {
        setSaveState("保存范围不确定，请重新载入", "error");
      } else {
        setSaveState("仍有未保存变更", "error");
      }
    },
    onRejected(error, context) {
      if (isActiveContext(context)) {
        setSaveState(
          hasUncertainSaveState(state) ? "保存范围不确定，请重新载入" : "保存失败",
          "error",
        );
        if (requiresDiscardReload(state)) elements.reload.disabled = false;
      }
      addActivity(error.message, "error");
      toast(`保存失败：${error.message}`, "error");
    },
    onSwitchPoison(error) {
      elements.reload.disabled = false;
      setConnection("error", "切换已冻结");
      setSaveState("迟到保存未确认，请重新载入", "error");
      addActivity(error.message, "error");
      toast(error.message, "error");
    },
    maxPendingSaves: MAX_PENDING_SAVES,
    getPayloadBytes: (savePayload) => measureSavePayloadBytes(
      savePayload,
      {
        maxChangeCount: MAX_CHANGE_COUNT,
        maxFileBytes: MAX_TEXT_BYTES,
        maxTotalBytes: MAX_SAVE_BYTES,
      },
    ),
    sanitizePayload: (savePayload) => sanitizeSavePayload(
      savePayload,
      { maxChangeCount: MAX_CHANGE_COUNT },
    ),
    maxPendingSaveBytes: MAX_PENDING_SAVE_BYTES,
  });
}

async function toggleFullscreen(value) {
  if (value === false || document.fullscreenElement) {
    if (document.fullscreenElement) await document.exitFullscreen();
    return false;
  }
  await document.documentElement.requestFullscreen();
  return true;
}

async function reloadCurrentDeck({ discardConfirmed = false } = {}) {
  const discardRequired = requiresDiscardReload(state);
  const discardAuthorization = discardRequired
    ? authorizeDiscardReload(state, { confirmed: discardConfirmed })
    : null;
  if (discardRequired) {
    if (!discardAuthorization) {
      throw new Error("重新载入前必须明确确认丢弃未保存的更改");
    }
  }
  if (!state.lastDeckPayload) {
    // There is no known-good in-memory payload after an initial load failure.
    // Once discard is explicitly confirmed, a full host reload is the only
    // operation that rebuilds the RPC connection from a known state.
    if (discardAuthorization) {
      state.confirmedDiscardUnload = true;
      window.location.reload();
    }
    return;
  }
  try {
    if (!state.remote || state.remoteUncertainty) {
      setConnection("connecting", "正在建立全新会话");
      addActivity("正在重建编辑器 iframe，并重新握手恢复已知文稿");
      await establishFreshRemoteSession({ editable: false });
      setConnection("connecting", "新会话已连接，正在恢复文稿");
    }
    const sourceLabel = elements.documentPath.textContent;
    if (state.source === "directory" && state.directoryHandle) {
      const switchLease = await beginDeckSwitch(discardAuthorization);
      try {
        if (
          !state.journalProjectScopeId
          || !state.journalAuthorizationStore
          || !state.journalTransactionLock
        ) {
          throw new Error("当前目录缺少稳定的保存恢复项目作用域，请重新打开文件夹");
        }
        await recoverProjectSaveIfNeeded(
          state.directoryHandle,
          sourceLabel,
          state.journalAuthorizationStore,
          state.journalTransactionLock,
        );
        const project = createProject({
          source: "directory",
          directoryHandle: state.directoryHandle,
          fileIndex: await indexProjectDirectory(state.directoryHandle),
          journalProjectScopeId: state.journalProjectScopeId,
          journalAuthorizationStore: state.journalAuthorizationStore,
          journalTransactionLock: state.journalTransactionLock,
        });
        await loadManifest(state.manifestPath, sourceLabel, project, { switchLease });
      } catch (error) {
        await cancelDeckSwitch(switchLease);
        throw error;
      }
      return;
    }
    if (state.source === "fallback") {
      const project = createProject({
        source: "fallback",
        fileIndex: state.fileIndex,
        readOnlyFallback: true,
      });
      await loadManifest(
        state.manifestPath,
        sourceLabel,
        project,
        { discardAuthorization },
      );
      return;
    }
    await setDeck(
      state.lastDeckPayload,
      sourceLabel,
      activeProject(),
      { discardAuthorization },
    );
  } catch (error) {
    abortDiscardReload(state, discardAuthorization);
    throw error;
  }
}

async function requestReloadCurrentDeck() {
  const discardRequired = requiresDiscardReload(state);
  if (discardRequired) {
    const uncertain = hasUncertainSaveState(state);
    const confirmed = window.confirm(
      uncertain
        ? "最近一次保存的影响范围无法确认。重新载入会永久丢弃编辑器中尚未确认保存的更改，是否继续？"
        : "最近一次保存失败，仍有未保存的更改。重新载入会永久丢弃这些更改，是否继续？",
    );
    if (!confirmed) {
      addActivity(
        uncertain
          ? "已取消重新载入；未确认的保存状态仍保持冻结"
          : "已取消重新载入；未保存的更改仍保留在编辑器中",
        "info",
      );
      return false;
    }
  }
  await reloadCurrentDeck({ discardConfirmed: discardRequired });
  return true;
}

function handleError(error, context = "操作失败") {
  const message = error?.message || String(error);
  setLoading(false);
  addActivity(`${context}：${message}`, "error");
  toast(`${context}：${message}`, "error");
  console.error(context, error);
}

function editorHostMethods() {
  return {
    close() {
      toast("当前文稿由 NeoDeck Local 托管");
    },
    reenter() {
      return requestReloadCurrentDeck();
    },
    toggleFullScreen: toggleFullscreen,
    showFeedback() {},
    sendPrompt() {
      toast("AI 提示词回传尚未接入", "warning");
    },
    showMessage(payload) {
      const message = typeof payload === "string"
        ? payload
        : payload?.message || payload?.content;
      if (message) toast(message);
    },
    hideMessage() {},
    onSave,
    getImages,
    setAnnotationMode() {},
    setAnnotationCurrentPage() {},
    upsertAnnotation() {},
    removeAnnotation() {},
    clearAnnotations() {},
  };
}

function loadFreshEditorFrame(sessionEpoch) {
  return new Promise((resolve, reject) => {
    const finish = (callback, value) => {
      clearTimeout(timeoutId);
      elements.frame.removeEventListener("load", onLoad);
      callback(value);
    };
    const onLoad = () => {
      if (state.remoteEpoch !== sessionEpoch) {
        finish(reject, new Error("iframe 加载属于已经失效的编辑器会话"));
        return;
      }
      finish(resolve);
    };
    const timeoutId = setTimeout(
      () => finish(reject, new Error("编辑器 iframe 加载超时")),
      20_000,
    );
    elements.frame.addEventListener("load", onLoad);
    elements.frame.src = `${EDITOR_ORIGIN}/neo-ppt/?${editorQuery()}`;
  });
}

async function establishFreshRemoteSession({ editable = true } = {}) {
  const previousConnection = state.connection;
  state.connection = null;
  state.remote = null;
  state.remoteEpoch += 1;
  const sessionEpoch = state.remoteEpoch;
  previousConnection?.destroy();

  let connection = null;
  try {
    await loadFreshEditorFrame(sessionEpoch);
    if (state.remoteEpoch !== sessionEpoch) {
      throw new Error("iframe 会话已经失效");
    }
    const messenger = new WindowMessenger({
      remoteWindow: elements.frame.contentWindow,
      allowedOrigins: [EDITOR_ORIGIN],
    });
    connection = connect({
      messenger,
      timeout: 20_000,
      methods: editorHostMethods(),
    });
    state.connection = connection;
    const remote = await connection.promise;
    if (
      state.remoteEpoch !== sessionEpoch
      || state.connection !== connection
    ) {
      connection.destroy();
      throw new Error("RPC 握手属于已经过期的编辑器会话");
    }
    state.remote = remote;
    await invokeCurrentRemote(remote, "setSlideConfig", [
      { editable, locale: "zh-CN", theme: "light" },
    ]);
    return remote;
  } catch (error) {
    if (state.remoteEpoch === sessionEpoch) {
      connection?.destroy();
      if (state.connection === connection) state.connection = null;
      state.remote = null;
      state.remoteEpoch += 1;
      elements.frame.removeAttribute("src");
    }
    throw error;
  }
}

async function connectEditor() {
  setConnection("connecting", "连接中");
  addActivity("正在连接公开 neo-ppt 编辑器");
  try {
    await establishFreshRemoteSession();
    setConnection("ready", "已连接");
    addActivity("编辑器 RPC 握手完成", "success");
    await openDemo();
  } catch (error) {
    setConnection("error", "连接失败");
    handleError(error, "编辑器连接失败");
    setLoading(true, "无法连接编辑器", "请检查网络，或 Kimi 是否更新了公开前端资源。");
  }
}

function setActivityPanel(open) {
  elements.activityPanel.classList.toggle("is-open", open);
  elements.activityPanel.setAttribute("aria-hidden", String(!open));
}

function setOpenDialog(open) {
  if (open && !elements.openDialog.open) elements.openDialog.showModal();
  if (!open && elements.openDialog.open) elements.openDialog.close();
  elements.openDialog.dataset.dropState = "idle";
  elements.uploadDropzone.classList.remove("is-dragging");
}

elements.openFolder.addEventListener("click", () => setOpenDialog(true));
elements.closeOpenDialog.addEventListener("click", () => setOpenDialog(false));
elements.chooseWritableFolder.addEventListener("click", pickDirectory);
elements.uploadFolder.addEventListener("click", () => elements.folderFallback.click());
elements.uploadDropzone.addEventListener("click", () => elements.folderFallback.click());
elements.openDialog.addEventListener("click", (event) => {
  if (event.target === elements.openDialog) setOpenDialog(false);
});
elements.openDemo.addEventListener("click", () => openDemo().catch((error) => handleError(error, "示例载入失败")));
elements.reload.addEventListener("click", () => {
  requestReloadCurrentDeck().catch((error) => handleError(error, "重新载入失败"));
});
elements.toggleActivity.addEventListener("click", () => setActivityPanel(!elements.activityPanel.classList.contains("is-open")));
elements.closeActivity.addEventListener("click", () => setActivityPanel(false));
elements.folderFallback.addEventListener("change", () => {
  if (elements.folderFallback.files?.length) {
    setOpenDialog(false);
    openFallbackFiles(elements.folderFallback.files).catch((error) => handleError(error, "上传文件夹失败"));
  }
  elements.folderFallback.value = "";
});

window.addEventListener("dragenter", (event) => {
  event.preventDefault();
  state.dragDepth += 1;
  setOpenDialog(true);
  elements.openDialog.dataset.dropState = "active";
  elements.uploadDropzone.classList.add("is-dragging");
});
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("dragleave", () => {
  state.dragDepth = Math.max(0, state.dragDepth - 1);
  if (state.dragDepth === 0) {
    elements.openDialog.dataset.dropState = "idle";
    elements.uploadDropzone.classList.remove("is-dragging");
  }
});
window.addEventListener("drop", async (event) => {
  event.preventDefault();
  state.dragDepth = 0;
  elements.openDialog.dataset.dropState = "loading";
  elements.uploadDropzone.classList.remove("is-dragging");
  try {
    const handles = await Promise.all([...event.dataTransfer.items].map((item) => item.getAsFileSystemHandle?.()));
    if (handles.some(Boolean)) await openDroppedFolder(handles);
    else if (event.dataTransfer.files?.length) {
      const files = [...event.dataTransfer.files];
      const roots = new Set(files.map((file) => file.webkitRelativePath?.split("/")[0]).filter(Boolean));
      const isSingleFolder = files.length > 0 && roots.size === 1 && files.every((file) => file.webkitRelativePath?.includes("/"));
      if (!isSingleFolder) throw new Error("只能拖入一个完整的 PPTD 项目文件夹，不能单独拖入 .pptd 文件");
      setOpenDialog(false);
      await openFallbackFiles(files);
    } else throw new Error("请拖入包含 .pptd、pages 和 media 的完整项目文件夹");
  } catch (error) {
    handleError(error, "拖放打开失败");
  } finally {
    elements.openDialog.dataset.dropState = "idle";
  }
});

window.addEventListener("beforeunload", (event) => {
  if (state.confirmedDiscardUnload) return;
  if (shouldWarnBeforeUnload(state, elements.saveState.dataset.kind)) {
    event.preventDefault();
    event.returnValue = "";
  }
});

window.neoDeck = {
  openDemo,
  getSlideStatus: () => (
    state.remote
      ? invokeCurrentRemote(
        state.remote,
        "getSlideStatus",
        [],
        { mayHaveApplied: false },
      )
      : undefined
  ),
  get status() {
    return {
      connected: Boolean(state.remote),
      source: state.source,
      title: state.deckTitle,
      manifestPath: state.manifestPath,
      indexedFiles: state.fileIndex.size,
      saveState: elements.saveState.dataset.kind,
    };
  },
};

connectEditor();
