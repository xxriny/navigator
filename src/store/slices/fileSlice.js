import { fileService } from "../../api/services/fileService";
import { platform } from "../../platform/index.js";

export const createFileSlice = (set, get) => ({
  openFiles: [],
  selectedFile: null,
  fileTree: [],
  projectFolder: null,

  setFileTree: (tree) => set({ fileTree: tree }),
  setSelectedFile: (file) => set({ selectedFile: file }),
  setProjectFolder: (path) => set({ projectFolder: path }),

  openFile: (file) => {
    const { openFiles } = get();
    const exists = openFiles.find((f) => f.id === file.id);
    const nextFiles = exists
      ? openFiles.map((f) => (f.id === file.id ? { ...f, ...file } : f))
      : [...openFiles, file];

    set({
      openFiles: nextFiles,
      selectedFile: file,
      activeViewportTab: { kind: "code", id: file.id },
    });
  },

  closeFile: (fileId) => {
    const { openFiles, activeViewportTab, lastOutputTab, resultData } = get();
    const filtered = openFiles.filter((f) => f.id !== fileId);
    const fallbackOutput = resultData ? "overview" : (lastOutputTab || "home");

    let nextViewport = activeViewportTab;
    if (activeViewportTab.kind === "code" && activeViewportTab.id === fileId) {
      nextViewport = filtered.length > 0
        ? { kind: "code", id: filtered[filtered.length - 1].id }
        : { kind: "output", id: fallbackOutput };
    }

    set({
      openFiles: filtered,
      selectedFile: filtered.find((f) => f.id === nextViewport.id) || null,
      activeViewportTab: nextViewport,
    });
  },

  updateOpenFileContent: (fileId, content) => {
    set((state) => {
      const nextFiles = state.openFiles.map((f) => (f.id === fileId ? { ...f, content } : f));
      const nextSelectedFile = state.selectedFile?.id === fileId ? { ...state.selectedFile, content } : state.selectedFile;
      return { openFiles: nextFiles, selectedFile: nextSelectedFile };
    });
  },

  detectLanguage: (filename) => {
    const ext = filename.split(".").pop()?.toLowerCase();
    const map = {
      py: "python", js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
      json: "json", md: "markdown", yaml: "yaml", yml: "yaml", html: "html", css: "css",
    };
    return map[ext] || "plaintext";
  },

  ensureProjectFolderAccess: async (folderPath) => {
    const { backendPort } = get();
    if (!backendPort || !folderPath) return false;
    try {
      const data = await fileService.scanFolder(backendPort, folderPath, 3, get().authToken);
      if (data.status === "ok") {
        set({ fileTree: data.tree || get().fileTree, projectFolder: data.root || folderPath });
        return true;
      }
      return false;
    } catch (e) {
      console.error("[ProjectAccess] Failed:", e);
      return false;
    }
  },

  selectAndScanFolder: async () => {
    const folderPath = await platform.selectFolder();
    if (!folderPath) return;
    get().ensureProjectFolderAccess(folderPath);
  },

  openProjectFile: async (node) => {
    const { backendPort, projectFolder } = get();
    if (!backendPort || !node?.path) return;

    const readFile = async () => fileService.readFile(backendPort, node.path, get().authToken);

    try {
      let data = await readFile();
      if (data.status !== "ok" && projectFolder) {
        const restored = await get().ensureProjectFolderAccess(projectFolder);
        if (restored) data = await readFile();
      }
      if (data.status === "ok") {
        get().openFile({
          id: node.path,
          name: node.name,
          path: node.path,
          content: data.content || "",
          language: get().detectLanguage(node.name),
        });
      }
    } catch (e) {
      console.error("[ReadFile] Failed:", e);
    }
  },
});
