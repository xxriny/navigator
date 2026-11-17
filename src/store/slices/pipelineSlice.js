import {
  EMPTY_RESULT_FIELDS,
  MODE_TO_ACTION_TYPE,
  MODE_TO_PIPELINE_TYPE,
  inferPipelineTypeFromResult,
  spreadResultData,
  normalizeMode
} from "../storeHelpers";

let _thinkingBuf = [];
let _thinkingTimer = null;

export const createPipelineSlice = (set, get) => ({
  pipelineStatus: "idle",
  pipelineError: null,
  pipelineNodes: {},
  thinkingLog: [],
  pipelineType: "analysis",
  resultData: null,
  agileImpactResult: null,
  ...EMPTY_RESULT_FIELDS,

  // idea_chat의 최근 응답에서 추출 — 🚀 Sync 버튼의 글로우/툴팁에 활용
  lastIdeaReady: false,
  lastIdeaSummary: "",
  lastSuggestedMode: null,
  // 매 idea_chat 응답에서 추출하는 후속 질문 4개 — ChatThread 하단에 칩으로 노출
  lastFollowups: [],

  // runSyncUpdate 진행 중 플래그 (완료 시 마커/메모 아카이브 처리에 사용)
  _syncInFlight: false,
  _syncMemoIdsForApply: [],
  _syncTargetVersion: null,

  devTrackingResult: null,
  devTrackingAnalyses: [],
  devTrackingForm: null,
  devTrackingShowManualRun: false,
  devTrackingRunning: false,
  devTrackingRunError: "",

  setAgileImpactResult: (result) => set({ agileImpactResult: result }),
  setDevTrackingResult: (result) => set({ devTrackingResult: result }),
  setDevTrackingAnalyses: (analyses) => set({ devTrackingAnalyses: analyses }),
  setDevTrackingForm: (form) => set({ devTrackingForm: form }),
  setDevTrackingShowManualRun: (val) => set({ devTrackingShowManualRun: val }),
  setDevTrackingRunning: (val) => set({ devTrackingRunning: val }),
  setDevTrackingRunError: (val) => set({ devTrackingRunError: val }),

  // 디버그 시스템
  debugLogs: [],
  addDebugLog: (log) => set((state) => ({
    debugLogs: [{ ...log, timestamp: Date.now() }, ...state.debugLogs].slice(0, 50)
  })),

  _handleWsMessage: (msg) => {
    const { type, node, data } = msg;
    const { analysisOwnerTeamId, currentUser } = get();
    // 분석을 시작한 팀과 현재 활성 팀이 다르면 → 백그라운드 팀에 라우팅
    const isBackground =
      analysisOwnerTeamId && analysisOwnerTeamId !== currentUser?.team_id;

    switch (type) {
      case "status":
        if (isBackground) {
          get()._updateParkedWorkspace(analysisOwnerTeamId, {
            pipelineNodes: {
              ...(get().teamWorkspaces[analysisOwnerTeamId]?.pipelineNodes || {}),
              [node]: data.status,
            },
          });
        } else {
          set((state) => ({ pipelineNodes: { ...state.pipelineNodes, [node]: data.status } }));
        }
        break;
      case "thinking":
        if (isBackground) {
          const prev = get().teamWorkspaces[analysisOwnerTeamId]?.thinkingLog || [];
          get()._updateParkedWorkspace(analysisOwnerTeamId, {
            thinkingLog: [...prev, { node, text: data.text, timestamp: Date.now() }].slice(-100),
          });
        } else {
          _thinkingBuf.push({ node, text: data.text, timestamp: Date.now() });
          if (!_thinkingTimer) {
            _thinkingTimer = setTimeout(() => {
              const msgs = _thinkingBuf.splice(0);
              _thinkingTimer = null;
              set((state) => ({ thinkingLog: [...state.thinkingLog, ...msgs].slice(-100) }));
            }, 200);
          }
        }
        break;
      case "result":
        if (isBackground) {
          // 백그라운드 팀 워크스페이스에 결과 저장
          get()._updateParkedWorkspace(analysisOwnerTeamId, {
            pipelineStatus: "done",
            ...spreadResultData(data),
          });
          set({ analysisOwnerTeamId: null });
          // 어느 팀 분석이 완료됐는지 알림
          const ownerTeam = get().myTeams.find((t) => t.id === analysisOwnerTeamId);
          const teamName = ownerTeam?.name || "다른 팀";
          get().addNotification(
            `"${teamName}" 분석 완료`,
            "success",
            { teamId: analysisOwnerTeamId, action: "switchTeam" },
          );
        } else {
          get()._processResult(data, node);
          set({ analysisOwnerTeamId: null });
        }
        break;
      case "error":
        if (isBackground) {
          get()._updateParkedWorkspace(analysisOwnerTeamId, {
            pipelineStatus: "error",
            pipelineError: data.message,
          });
          set({ analysisOwnerTeamId: null });
        } else {
          set({ pipelineStatus: "error", pipelineError: data.message });
          set({ analysisOwnerTeamId: null });
          // sync 진행 중 에러 발생 시 플래그 정리 (스냅샷은 그대로 두어 수동 롤백 가능)
          if (get()._syncInFlight) {
            set({ _syncInFlight: false, _syncMemoIdsForApply: [], _syncTargetVersion: null });
          }
          get().addDebugLog({
            level: "error",
            message: `Pipeline WS Error: ${data.message}`,
            rawData: data
          });
          get().addNotification(`파이프라인 오류: ${data.message}`, "error");
        }
        break;
      case "rag_retrieval":
      case "rag_status":
        break;
    }
  },

  /** parked workspace의 특정 필드만 업데이트 */
  _updateParkedWorkspace: (teamId, patch) => {
    set((s) => ({
      teamWorkspaces: {
        ...s.teamWorkspaces,
        [teamId]: { ...(s.teamWorkspaces[teamId] || {}), ...patch },
      },
    }));
  },

  _processResult: (data, node = "complete") => {
    if (node === "idea_chat") {
      const reply = data.chat_reply || data.assistant_message || "";
      if (reply) {
        set((state) => ({ chatHistory: [...state.chatHistory, { role: "assistant", content: reply }] }));
      }

      // 🚀 Sync 버튼의 글로우/넛지 트리거 + 후속 질문 칩
      set({
        lastIdeaReady: !!data.idea_ready,
        lastIdeaSummary: data.idea_summary || "",
        lastSuggestedMode: data.suggested_mode || null,
        lastFollowups: Array.isArray(data.suggested_followups)
          ? data.suggested_followups
              .map((s) =>
                typeof s === "string"
                  ? { question: s.trim(), domain: "general" }
                  : s
              )
              .filter((s) => s?.question?.trim())
              .slice(0, 4)
          : [],
      });

      // 채팅에서 생성된 자동 메모(notes_to_add)를 userComments에 머지.
      // 백엔드(pipeline_runner)가 이미 SQLite에 영속화했으므로 여기서는 로컬 상태만 갱신.
      // id 기준 dedupe — 동일 frame 재도달 또는 syncMemos 결과와 겹치는 경우 대비.
      const notes = data.notes_to_add || [];
      if (notes.length > 0) {
        const existingIds = new Set((get().userComments || []).map((c) => c.id));
        const newMemos = notes
          .filter((n) => n && n.id && !existingIds.has(n.id))
          .map((n) => ({
            id: n.id,
            text: n.text || "",
            selectedText: n.selected_text || "",
            section: n.section || "Idea Chat",
            detail: n.detail || "",
            applied: false,
            appliedAt: null,
            createdAt: Date.now(),
          }));
        if (newMemos.length > 0) {
          set((state) => ({ userComments: [...state.userComments, ...newMemos] }));
          // 사용자에게 추가 알림 (2.5초 자동 dismiss)
          get().addNotification(`메모 ${newMemos.length}건이 추가되었습니다.`, "success", 2500);
          // race-avoidance: MemoManager 마운트 시 시작된 syncMemos가 늦게 응답해
          // 방금 push한 항목을 덮어쓰지 않도록, 짧은 지연 뒤 서버 스냅샷과 강제 정합.
          // (백엔드가 이미 persist했으므로 server fetch에 동일 id가 포함됨)
          setTimeout(() => { get().syncMemos(); }, 250);
        }
      }
      return;
    }

    // AI 어드바이저 제안사항을 메모로 자동 변환
    const recommendations = data.recommendations || data.sa_advisor_output?.recommendations || [];
    if (recommendations.length > 0) {
      const existingTexts = new Set(get().userComments.map(c => c.text));
      const newMemos = recommendations
        .filter(r => !existingTexts.has(r.action))
        .map(r => ({
          id: `auto_${Date.now()}_${Math.random().toString(36).substr(2, 5)}`,
          text: r.action,
          selectedText: r.target,
          section: `Advisor (${r.priority})`,
          createdAt: Date.now(),
        }));
      
      if (newMemos.length > 0) {
        // 백엔드에도 동기화 (선택 사항, 여기서는 로컬 UI 우선 반영)
        set((state) => ({ userComments: [...state.userComments, ...newMemos] }));
      }
    }

    // UPDATE 모드에서 components/apis/tables는 백엔드 결과를 그대로 사용.
    // 백엔드 component_scheduler/sa_unified_modeler의 UPDATE_PROMPT가 이미
    // 기존 항목 보존 + 신규 항목 추가를 처리하므로 프론트엔드 병합 불필요.
    // (이전 스토어 데이터와 병합하면 stale 세션 데이터가 섞여 로컬과 공유 DB 표시가 달라지는 버그 발생)

    const nextResultData = {
      pipelineStatus: "done",
      pipelineType: inferPipelineTypeFromResult(data),
      ...spreadResultData(data),
      chatHistory: data.chat_history || get().chatHistory,
      activeViewportTab: { kind: "output", id: "memo" }, // 리포트 대신 메모 탭으로 즉시 이동
      lastOutputTab: "memo",
    };

    set(nextResultData);

    // 🚀 Sync 완료 처리 — runSyncUpdate가 띄운 플래그를 보고 마커 삽입 + 메모 archive
    if (get()._syncInFlight) {
      const targetVersion = get()._syncTargetVersion || "v1.0";
      const memoIdsForApply = get()._syncMemoIdsForApply || [];
      const snapshotIndex = (get().designSnapshots || []).length - 1;

      set((state) => ({
        chatHistory: [
          ...state.chatHistory,
          {
            role: "system_marker",
            kind: "sync",
            version: targetVersion,
            appliedAt: new Date().toISOString(),
            snapshotIndex,
          },
        ],
        _syncInFlight: false,
        _syncMemoIdsForApply: [],
        _syncTargetVersion: null,
        lastIdeaReady: false,
        lastIdeaSummary: "",
        lastFollowups: [],
      }));

      // 메모 일괄 archive — 로컬은 markMemosApplied 내부에서 패치, 서버 영속화도 동시
      if (memoIdsForApply.length > 0) {
        get().markMemosApplied(memoIdsForApply, { reflectedVersion: targetVersion });
        get().addNotification(
          `${targetVersion} 반영 완료 · 메모 ${memoIdsForApply.length}건 보관`,
          "success",
          3000
        );
      } else {
        get().addNotification(`${targetVersion} 반영 완료`, "success", 3000);
      }
    }
  },

  startAnalysis: (idea, context = "", apiKey = "", model = "gemini-3.1-flash-lite", selectedMode = "create", initialTitle = null) => {
    const { currentUser } = get();
    if (!currentUser?.team_id) {
      get().addNotification("팀에 가입한 사용자만 LLM 분석을 실행할 수 있습니다.", "error");
      return;
    }
    const normalizedMode = normalizeMode(selectedMode);
    const sourceDir = get().projectFolder || "";
    get().createSession(initialTitle);
    // 분석 시작한 팀 등록 (팀 전환 시 백그라운드 라우팅에 사용)
    set({ analysisOwnerTeamId: currentUser?.team_id || null });

    set({
      pipelineStatus: "running",
      pipelineError: null,
      pipelineNodes: {},
      thinkingLog: [],
      pipelineType: MODE_TO_PIPELINE_TYPE[normalizedMode] || "analysis_create",
      // EMPTY_RESULT_FIELDS 중 데이터 표시와 직결되는 핵심 필드만 초기화 (점진적 업데이트를 위함)
      // resultData: null, // 기존 데이터를 바로 날리지 않고 새 결과가 오면 덮어씁니다.
      // 첫 프롬프트를 chat seed로 사용 — 이후 HomeScreen이 conversation 모드를 유지하고
      // 사용자가 idea_chat으로 후속 대화를 이어갈 수 있도록 함.
      chatHistory: idea ? [{ role: "user", content: idea }] : [],
      chatInput: "",
      selectedMode: normalizedMode,
      activeViewportTab: { kind: "output", id: "progress" },
      lastOutputTab: "progress",
    });
    get().sendWsMessage("analyze", {
      idea, context, api_key: apiKey, model,
      action_type: MODE_TO_ACTION_TYPE[normalizedMode],
      source_dir: sourceDir,
      auth_token: get().authToken,
    });
  },

  /**
   * 🚀 Pre-flight 요약 추출 — 무거운 파이프라인에 보낼 idea 페이로드를
   * "최종 확정 요구사항"으로 정제한다. SyncConfirmModal이 호출.
   *   returns { status, summary_markdown, dropped_points, error? }
   */
  extractFinalIdea: async () => {
    const { backendPort, chatHistory, userComments, apiKey, model, authToken } = get();
    if (!backendPort) {
      return {
        status: "error",
        summary_markdown: "",
        dropped_points: [],
        error: "백엔드와 연결되어 있지 않습니다.",
      };
    }
    // 마지막 sync 마커 이후의 대화만 정제 대상 (UPDATE) — 마커가 없으면 전체 (CREATE)
    const lastSyncIdx = (() => {
      for (let i = (chatHistory || []).length - 1; i >= 0; i--) {
        const m = chatHistory[i];
        if (m && m.role === "system_marker" && m.kind === "sync") return i;
      }
      return -1;
    })();
    const chatDiff = (chatHistory || [])
      .slice(lastSyncIdx + 1)
      .filter((m) => m && (m.role === "user" || m.role === "assistant"))
      .map((m) => ({ role: m.role, content: m.content || "" }));
    const activeMemos = (userComments || [])
      .filter((c) => !c.applied)
      .map((c) => ({
        text: c.text || "",
        section: c.section || "",
        detail: c.detail || "",
      }));

    console.log("[extractFinalIdea] authToken present:", !!authToken, "| chatDiff:", chatDiff.length, "msgs | memos:", activeMemos.length);

    try {
      const { ideaService } = await import("../../api/services/ideaService");
      const res = await ideaService.extractFinalIdea(backendPort, {
        chat_history: chatDiff,
        memos: activeMemos,
        api_key: apiKey || "",
        model: model || "gemini-3.1-flash-lite",
        auth_token: authToken || "",
      });
      console.log("[extractFinalIdea] response:", res?.status, "| summary_len:", res?.summary_markdown?.length ?? 0, "| error:", res?.error);
      return res;
    } catch (e) {
      console.error("[extractFinalIdea] fetch failed:", e);
      return {
        status: "error",
        summary_markdown: "",
        dropped_points: [],
        error: e?.message || String(e),
      };
    }
  },

  /**
   * 🚀 통합 Sync — 메인 화면의 하단 버튼이 호출하는 유일한 진입점.
   * designSnapshots가 비어있으면 첫 빌드(CREATE), 있으면 기존 산출물에 변경분을
   * 더하는 UPDATE 모드로 라우팅한다. 완료는 _processResult에서 처리.
   *
   * @param {{ ideaOverride?: string }} [opts]
   *   ideaOverride: SyncConfirmModal이 Pre-flight 요약(노이즈 제거된 마크다운)을
   *   넘겨주면 그 텍스트를 idea로 사용한다. 미지정 시 기존처럼 날것 대화/메모를 합성.
   */
  runSyncUpdate: (opts = {}) => {
    const { currentUser } = get();
    if (!currentUser?.team_id) {
      get().addNotification("팀에 가입한 사용자만 LLM 분석을 실행할 수 있습니다.", "error");
      return;
    }
    if (get().pipelineStatus === "running") {
      get().addNotification("이미 분석이 진행 중입니다.", "warning");
      return;
    }
    const {
      chatHistory,
      userComments,
      apiKey,
      model,
      projectFolder,
      resultData,
    } = get();

    // 0) 첫 빌드 여부 판별 — 산출물(resultData)이 아직 없으면 CREATE, 있으면 UPDATE.
    //    designSnapshots는 첫 빌드 시 push되지 않기 때문에 길이만으로 판별하면 두 번째
    //    Sync에서도 또 CREATE로 빠지는 회귀가 발생한다(버전 정체 + 롤백 마커 누락).
    const isFirstBuild = !resultData;
    const actionType = isFirstBuild ? "CREATE" : "UPDATE";
    const pipelineTypeKey = isFirstBuild ? "analysis_create" : "analysis_update";

    // 1) 마지막 sync 마커 이후의 대화만 추출 (UPDATE는 토큰 절약, CREATE는 마커가 없어 전체)
    const lastSyncIdx = (() => {
      for (let i = (chatHistory || []).length - 1; i >= 0; i--) {
        const m = chatHistory[i];
        if (m && m.role === "system_marker" && m.kind === "sync") return i;
      }
      return -1;
    })();
    const chatDiff = (chatHistory || [])
      .slice(lastSyncIdx + 1)
      .filter((m) => m && (m.role === "user" || m.role === "assistant"));

    // 2) 활성 메모 (applied=false)
    const activeMemos = (userComments || []).filter((c) => !c.applied);

    // 3) 둘 다 비면 호출 의미 없음
    if (chatDiff.length === 0 && activeMemos.length === 0) {
      get().addNotification(
        isFirstBuild
          ? "설계서를 생성하려면 먼저 아이디어를 대화로 정리하거나 메모를 추가해주세요."
          : "반영할 새 대화나 활성 메모가 없습니다.",
        "warning",
        3000
      );
      return;
    }

    // 4) idea 페이로드 빌드
    //    - Pre-flight 모달에서 정제된 요약(ideaOverride)이 들어오면 그것을 그대로 사용 (권장 경로)
    //    - 없으면 fallback으로 날것의 대화/메모를 합성
    const overrideText = (opts.ideaOverride || "").trim();
    let idea;
    if (overrideText) {
      const header = isFirstBuild
        ? "[대화·메모에서 정제된 최종 요구사항 — 첫 빌드]"
        : "[대화·메모에서 정제된 변경 요청 — UPDATE]";
      idea = `${header}\n\n${overrideText}`;
    } else {
      const diffLines = chatDiff
        .map((m) => `[${m.role === "user" ? "사용자" : "AI"}] ${m.content}`)
        .join("\n\n");
      const memoLines = activeMemos
        .map((m, i) => {
          const sec = m.section || "일반";
          const detail = m.detail && m.detail.trim() ? `\n   ${m.detail.trim()}` : "";
          return `${i + 1}. [${sec}] ${m.text}${detail}`;
        })
        .join("\n");
      const ideaParts = [
        isFirstBuild
          ? "[대화로 정리된 프로젝트 요구사항 — 첫 빌드]"
          : "[이전 동기화 이후 사용자가 요청한 변경 사항]",
      ];
      if (diffLines) {
        ideaParts.push((isFirstBuild ? "\n## 대화 내역\n" : "\n## 새 대화 내역\n") + diffLines);
      }
      if (memoLines) {
        ideaParts.push((isFirstBuild ? "\n## 추가 메모\n" : "\n## 반영 대기 메모\n") + memoLines);
      }
      idea = ideaParts.join("\n");
    }

    // 5) prev-design context — CREATE면 비우고, UPDATE면 기존 산출물 직렬화
    let prevContext = "";
    let previousFeatures = [];
    if (!isFirstBuild) {
      const prevRtm = get().requirements_rtm || [];
      const prevDesign = {
        requirements_rtm: prevRtm,
        components: get().components || [],
        apis: get().apis || [],
        tables: get().tables || [],
        tech_stacks: get().tech_stacks || [],
      };
      prevContext = `[이전 분석 결과 — 반드시 아래 설계를 기반으로 업데이트하세요]\n${JSON.stringify(prevDesign, null, 2)}`;
      previousFeatures = prevRtm
        .map((r) => ({
          id: r.feature_id || r.id || r.REQ_ID || "",
          label: r.label || "",
          desc: r.description || r.desc || "",
          cat: r.category || r.cat || "",
          pri: r.priority || r.pri || "",
          deps: r.dependencies || r.deps || [],
          tc: r.test_criteria || r.tc || "",
        }))
        .filter((f) => f.id);
    }

    // 6) 스냅샷 푸시 — CREATE는 "이전 상태"가 없어 스킵, 결과 자체가 v1.0이 됨
    let targetVersion;
    if (isFirstBuild) {
      targetVersion = "v1.0";
    } else {
      const counterBefore = get().designSnapshotCounter || 0;
      get().pushDesignSnapshot();
      targetVersion = `v1.${counterBefore + 1}`;
    }

    // 7) sync 플래그 + 진행 상태
    const memoIdsForApply = activeMemos
      .map((m) => m.id)
      .filter((id) => id && !String(id).startsWith("temp_"));

    set({
      _syncInFlight: true,
      _syncMemoIdsForApply: memoIdsForApply,
      _syncTargetVersion: targetVersion,
      pipelineStatus: "running",
      pipelineError: null,
      pipelineNodes: {},
      thinkingLog: [],
      pipelineType: pipelineTypeKey,
      agileImpactResult: null,
      activeViewportTab: { kind: "output", id: "progress" },
      lastOutputTab: "progress",
      analysisOwnerTeamId: currentUser?.team_id || null,
    });

    get().sendWsMessage("analyze", {
      idea,
      context: prevContext,
      api_key: apiKey || "",
      model: model || "gemini-3.1-flash-lite",
      action_type: actionType,
      source_dir: projectFolder || "",
      auth_token: get().authToken,
      previous_features: previousFeatures,
    });
  },

  sendIdeaChat: (message, apiKey, model) => {
    const text = (message || "").trim();
    if (!text) return;

    // 새 메시지 송신 시 이전 후속 질문 칩은 즉시 비움 (stale 방지)
    set({ lastFollowups: [] });

    // outbound chat_history는 백엔드 Pydantic이 {role, content}만 받으므로
    // system_marker(sync/rollback)는 반드시 제거. 보낼 때 단일 책임 위치에서 처리.
    const all = (get().chatHistory || []).filter(
      (m) => m && (m.role === "user" || m.role === "assistant")
    );
    const last = all[all.length - 1];
    const history =
      last && last.role === "user" && last.content === text
        ? all.slice(0, -1)
        : all;

    get().sendWsMessage("idea_chat", {
      message: text,
      chat_history: history,
      previous_result: get().resultData || {},
      api_key: apiKey || "",
      model: model || "gemini-3.1-flash-lite",
      session_id: get().currentSessionId || "",
      auth_token: get().authToken,
    });
  },

  resetPipeline: () => set((state) => ({
    pipelineStatus: "idle",
    pipelineError: null,
    pipelineNodes: {},
    thinkingLog: [],
    ...EMPTY_RESULT_FIELDS,
    activeViewportTab: { kind: "output", id: "home" },
    chatHistory: [],
    // userComments: [], // 메모 유지
  })),
});
