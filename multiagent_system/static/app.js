const state = {
  session: null,
  sessions: [],
  workspaces: [],
  current_session_id: "",
  agents: [],
  events: [],
  acp: {
    command: "",
    default_agent_name: "",
    available_agents: [],
  },
  polling: null,
  ui: {
    settingsOpen: false,
    sessionModalOpen: false,
    agentModalOpen: false,
    stickToBottom: true,
    lastSnapshotKey: "",
    expandedTools: {},
  },
  mention: {
    open: false,
    query: "",
    start: -1,
    end: -1,
    activeIndex: 0,
    options: [],
  },
};

const agentForm = document.querySelector("#agent-form");
const messageForm = document.querySelector("#message-form");
const agentList = document.querySelector("#agent-list");
const timeline = document.querySelector("#timeline");
const agentCount = document.querySelector("#agent-count");
const heroAgentCount = document.querySelector("#hero-agent-count");
const heroEventCount = document.querySelector("#hero-event-count");
const timelineCount = document.querySelector("#timeline-count");
const sessionSubtitle = document.querySelector("#session-subtitle");
const composerHint = document.querySelector("#composer-hint");
const stopDiscussionsButton = document.querySelector("#stop-discussions");
const mentionMenu = document.querySelector("#mention-menu");
const messageInput = document.querySelector("#message-input");
const settingsOverlay = document.querySelector("#settings-overlay");
const settingsDrawer = document.querySelector("#settings-drawer");
const openSettingsButton = document.querySelector("#open-settings");
const closeSettingsButton = document.querySelector("#close-settings");
const openSessionModalButton = document.querySelector("#open-session-modal");
const closeSessionModalButton = document.querySelector("#close-session-modal");
const cancelSessionModalButton = document.querySelector("#cancel-session-modal");
const sessionModal = document.querySelector("#session-modal");
const sessionForm = document.querySelector("#session-form");
const sessionTitleInput = document.querySelector("#session-title");
const sessionWorkspaceSelect = document.querySelector("#session-workspace");
const sessionWorkspaceHint = document.querySelector("#session-workspace-hint");
const sessionList = document.querySelector("#session-list");
const openAgentModalButton = document.querySelector("#open-agent-modal");
const closeAgentModalButton = document.querySelector("#close-agent-modal");
const cancelAgentModalButton = document.querySelector("#cancel-agent-modal");
const agentModal = document.querySelector("#agent-modal");
const agentNameInput = document.querySelector("#agent-name");
const agentTransportSelect = document.querySelector("#agent-transport");
const agentAcpNameInput = document.querySelector("#agent-acp-name");
const agentAcpCommandInput = document.querySelector("#agent-acp-command");
const agentAcpNameField = document.querySelector("#agent-acp-name-field");
const agentAcpCommandField = document.querySelector("#agent-acp-command-field");
const agentStdioCommandField = document.querySelector("#agent-stdio-command-field");
const agentStdioCommandInput = document.querySelector("#agent-stdio-command");
const agentAcpHint = document.querySelector("#agent-acp-hint");
const acpAgentOptions = document.querySelector("#acp-agent-options");
const agentTemplate = document.querySelector("#agent-template");
const sessionTemplate = document.querySelector("#session-template");
const eventTemplate = document.querySelector("#event-template");

agentList.dataset.empty = "还没有 agent。点击右上角设置后新建角色。";
timeline.dataset.empty = "还没有协作事件。发送一条消息后，这里会显示完整的接力轨迹。";
sessionList.dataset.empty = "还没有会话。先创建一个工作会话。";

function fillDatalist(list, options) {
  list.innerHTML = "";
  for (const item of options || []) {
    const option = document.createElement("option");
    option.value = item.name;
    option.label = item.description || item.name;
    list.appendChild(option);
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let payload = {};
  if (text.trim()) {
    try {
      payload = JSON.parse(text);
    } catch {
      throw new Error(text.trim() || `request failed with status ${response.status}`);
    }
  }
  if (!response.ok) {
    throw new Error(payload.error || "request failed");
  }
  return payload;
}

function formatTime(timestamp) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestamp * 1000));
}

function isNearBottom(element, threshold = 64) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= threshold;
}

const markdownRenderer = window.markdownit({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false,
});

function normalizeMarkdown(text) {
  let source = String(text ?? "").replace(/\r\n/g, "\n").trim();
  if (!source) {
    return "";
  }

  source = source
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n");

  // 将被压扁的 fenced code block 拆回独立行。
  source = source.replace(/\s*```([^\n`]*)\s*/g, "\n```$1\n");

  // 将行内出现的标题标记拆到新行，兼容 emoji 或中文后直接接 ## 的输出。
  source = source.replace(/([^\n])\s*(#{1,6})(?=\S)/g, "$1\n$2 ");

  // 将常见的列表项拆到新行，避免 “概览项目:1.xxx2.xxx” 这种一整段粘连。
  source = source.replace(/([^\n])\s+([-*])\s+(?=\S)/g, "$1\n$2 ");
  source = source.replace(/([^\n])\s+(\d+\.)\s+(?=\S)/g, "$1\n$2 ");

  // 常见 emoji 章节标题单独成行，提升可读性。
  source = source.replace(/([^\n])\s*((?:📍|📁|🎯|🎨|✅|⚠️|🧭|🕵️‍♀️|🛠️|🚀)+)(?=\S)/g, "$1\n$2 ");

  // 代码块结束后若直接跟正文或标题，补一个换行。
  source = source.replace(/```(\n?)(?=[^\n`#-*>\s])/g, "```\n");

  return source
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function renderMarkdownFallback(text) {
  const escaped = String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
  return `<p>${escaped.replaceAll("\n", "<br>")}</p>`;
}

function renderMarkdown(text) {
  const source = normalizeMarkdown(text);
  if (!source.trim()) {
    return "<p>(无内容)</p>";
  }
  try {
    const rendered = markdownRenderer.render(source);
    return window.DOMPurify.sanitize(rendered, {
      USE_PROFILES: { html: true },
    });
  } catch (error) {
    console.error("markdown render failed", error);
    return renderMarkdownFallback(source);
  }
}

function hasRunningAgent() {
  return state.agents.some((agent) => agent.status === "running");
}

function buildSnapshotKey(events) {
  if (events.length === 0) {
    return "empty";
  }
  const lastEvent = events[events.length - 1];
  return `${events.length}:${lastEvent.id}:${lastEvent.updated_at}:${lastEvent.text?.length || 0}`;
}

function setComposerHint() {
  if (state.mention.open && state.mention.options.length > 0) {
    composerHint.textContent = "回车或 Tab 插入当前提示，方向键切换候选项。";
    return;
  }
  if (hasRunningAgent()) {
    composerHint.textContent = "讨论进行中。点击“停止讨论”可以中断当前 agent 运行。";
    return;
  }
  composerHint.textContent = "未写 `@` 时会广播给全部 agent。";
}

function openSettings() {
  state.ui.settingsOpen = true;
  settingsOverlay.classList.remove("hidden");
  settingsDrawer.classList.remove("hidden");
  document.body.classList.add("no-scroll");
}

function closeSettings() {
  state.ui.settingsOpen = false;
  settingsDrawer.classList.add("hidden");
  if (!state.ui.agentModalOpen && !state.ui.sessionModalOpen) {
    settingsOverlay.classList.add("hidden");
    document.body.classList.remove("no-scroll");
  }
}

function renderWorkspaceOptions() {
  sessionWorkspaceSelect.innerHTML = "";
  for (const workspace of state.workspaces) {
    const option = document.createElement("option");
    option.value = workspace.id;
    option.textContent = workspace.name;
    sessionWorkspaceSelect.appendChild(option);
  }
  if (!sessionWorkspaceSelect.value && state.workspaces.length > 0) {
    sessionWorkspaceSelect.value = state.workspaces[0].id;
  }
  updateWorkspaceHint();
}

function updateWorkspaceHint() {
  const workspace = state.workspaces.find((item) => item.id === sessionWorkspaceSelect.value);
  if (!workspace) {
    sessionWorkspaceHint.textContent = "";
    return;
  }
  sessionWorkspaceHint.textContent = `${workspace.description || "工作环境"} · ${workspace.cwd}`;
}

function openSessionModal() {
  state.ui.sessionModalOpen = true;
  settingsOverlay.classList.remove("hidden");
  sessionModal.classList.remove("hidden");
  document.body.classList.add("no-scroll");
  renderWorkspaceOptions();
  window.setTimeout(() => sessionTitleInput.focus(), 20);
}

function closeSessionModal() {
  state.ui.sessionModalOpen = false;
  sessionModal.classList.add("hidden");
  sessionForm.reset();
  updateWorkspaceHint();
  if (!state.ui.settingsOpen && !state.ui.agentModalOpen) {
    settingsOverlay.classList.add("hidden");
    document.body.classList.remove("no-scroll");
  }
}

function openAgentModal() {
  state.ui.agentModalOpen = true;
  settingsOverlay.classList.remove("hidden");
  agentModal.classList.remove("hidden");
  document.body.classList.add("no-scroll");
  agentAcpCommandInput.value = state.acp.command || "";
  agentAcpNameInput.value = state.acp.default_agent_name || "";
  agentTransportSelect.value = "acp";
  agentStdioCommandInput.value = "python3 -m nocode_agent.backend_stdio";
  syncTransportFields();
  renderAcpHints();
  window.setTimeout(() => agentNameInput.focus(), 20);
}

function closeAgentModal() {
  state.ui.agentModalOpen = false;
  agentModal.classList.add("hidden");
  agentForm.reset();
  if (!state.ui.settingsOpen && !state.ui.sessionModalOpen) {
    settingsOverlay.classList.add("hidden");
    document.body.classList.remove("no-scroll");
  }
}

function renderSessions() {
  sessionList.innerHTML = "";
  const currentId = state.current_session_id;
  if (state.session) {
    sessionSubtitle.textContent = `${state.session.title} · ${state.session.cwd}`;
  } else {
    sessionSubtitle.textContent = "当前会话";
  }

  for (const session of state.sessions) {
    const node = sessionTemplate.content.firstElementChild.cloneNode(true);
    const workspace = state.workspaces.find((item) => item.id === session.workspace_id);
    node.querySelector(".session-name").textContent = session.title;
    node.querySelector(".session-workspace").textContent = workspace
      ? `${workspace.name}`
      : session.workspace_id || "未命名环境";
    node.querySelector(".session-cwd").textContent = session.cwd;
    const badge = node.querySelector(".session-badge");
    const selected = session.id === currentId;
    badge.classList.toggle("hidden", !selected);
    node.classList.toggle("active", selected);
    node.addEventListener("click", async () => {
      if (selected) {
        return;
      }
      try {
        await api("/api/sessions/select", {
          method: "POST",
          body: JSON.stringify({ session_id: session.id }),
        });
        state.ui.stickToBottom = true;
        closeMentionMenu();
        await refresh();
      } catch (error) {
        window.alert(error.message);
      }
    });
    sessionList.appendChild(node);
  }
}

function renderAgents() {
  agentList.innerHTML = "";
  const count = String(state.agents.length);
  agentCount.textContent = count;
  heroAgentCount.textContent = count;

  for (const agent of state.agents) {
    const node = agentTemplate.content.firstElementChild.cloneNode(true);
    node.querySelector(".agent-name").textContent = `@${agent.name}`;
    node.querySelector(".agent-thread").textContent = agent.thread_id || "-";
    const transportLabel = `Transport: ${agent.transport || "acp"}`;
    const acpLabel = agent.transport === "stdio"
      ? `STDIO: ${agent.stdio_command || "-"}`
      : (agent.acp_agent_name ? `ACP: ${agent.acp_agent_name} via ${agent.acp_command}` : "");
    node.querySelector(".agent-prompt").textContent =
      [agent.system_prompt || "未设置专属角色设定。", transportLabel, acpLabel].filter(Boolean).join("\n");

    const status = node.querySelector(".agent-status");
    status.textContent = agent.status;
    status.dataset.status = agent.status;

    const stopButton = node.querySelector(".agent-stop-button");
    stopButton.disabled = agent.status !== "running";
    stopButton.addEventListener("click", async () => {
      try {
        await api(`/api/agents/${agent.id}/stop`, { method: "POST", body: "{}" });
        await refresh();
      } catch (error) {
        window.alert(error.message);
      }
    });

    node.querySelector(".agent-clear-button").addEventListener("click", async () => {
      try {
        await api(`/api/agents/${agent.id}/clear`, { method: "POST", body: "{}" });
        await refresh();
      } catch (error) {
        window.alert(error.message);
      }
    });

    agentList.appendChild(node);
  }

  stopDiscussionsButton.disabled = !hasRunningAgent();
  setComposerHint();
}

function renderAcpHints() {
  const transport = agentTransportSelect.value || "acp";
  const availableAgents = state.acp.available_agents || [];
  fillDatalist(acpAgentOptions, availableAgents);

  if (transport === "stdio") {
    agentAcpHint.textContent = "STDIO 模式会通过本地命令启动 agent 进程并复用它的会话。";
    return;
  }

  if (state.acp.command) {
    const name = state.acp.default_agent_name || "未选择";
    const count = availableAgents.length;
    agentAcpHint.textContent = `最近一次探测：${state.acp.command}，默认 agent：${name}，发现 ${count} 个 manifest。`;
  } else {
    agentAcpHint.textContent = "填写这个 agent 要启动的 ACP 命令，以及期望连接的 agent 名。";
  }
}

function syncTransportFields() {
  const transport = agentTransportSelect.value || "acp";
  const stdio = transport === "stdio";
  agentAcpNameField.classList.toggle("hidden", stdio);
  agentAcpCommandField.classList.toggle("hidden", stdio);
  agentStdioCommandField.classList.toggle("hidden", !stdio);
  renderAcpHints();
}

async function refreshAgentAcpSuggestions() {
  const command = agentAcpCommandInput.value.trim();
  const agentName = agentAcpNameInput.value.trim();
  if (!command) {
    state.acp = { ...state.acp, command: "", available_agents: [] };
    renderAcpHints();
    return;
  }

  try {
    if ((agentTransportSelect.value || "acp") !== "acp") {
      return;
    }
    const result = await api("/api/acp", {
      method: "POST",
      body: JSON.stringify({
        command,
        default_agent_name: agentName || undefined,
      }),
    });
    state.acp = result;
    if (!agentAcpNameInput.value.trim() && result.default_agent_name) {
      agentAcpNameInput.value = result.default_agent_name;
    }
    renderAcpHints();
  } catch (error) {
    agentAcpHint.textContent = `ACP 探测失败：${error.message}`;
  }
}

function renderMentions(container, mentions) {
  container.innerHTML = "";
  if (!mentions || mentions.length === 0) {
    return;
  }
  for (const mention of mentions) {
    const pill = document.createElement("span");
    pill.className = "mention-pill";
    pill.textContent = `@${mention}`;
    container.appendChild(pill);
  }
}

function isTimelineSelectionActive() {
  const selection = document.getSelection();
  if (!selection || selection.type !== "Range" || selection.rangeCount === 0) {
    return false;
  }
  const anchorNode = selection.anchorNode;
  return Boolean(anchorNode && timeline.contains(anchorNode));
}

function setExpandedToolState(key, open) {
  if (open) {
    state.ui.expandedTools[key] = true;
    return;
  }
  delete state.ui.expandedTools[key];
}

function renderToolEvents(container, tools, eventId) {
  container.innerHTML = "";
  if (!tools || tools.length === 0) {
    container.parentElement.classList.add("hidden");
    return;
  }

  container.parentElement.classList.remove("hidden");
  const groups = new Map();

  for (const tool of tools) {
    const toolCallId = tool.tool_call_id || `${tool.name || "tool"}:${groups.size}`;
    const current = groups.get(toolCallId) || {
      name: tool.name || "tool",
      args: null,
      output: null,
    };
    if (tool.type === "tool_start") {
      current.args = tool.args ?? {};
    } else if (tool.type === "tool_end") {
      current.output = tool.output ?? "";
    }
    groups.set(toolCallId, current);
  }

  const panel = document.createElement("details");
  panel.className = "tool-panel";
  const panelKey = `${eventId}:panel`;
  panel.open = Boolean(state.ui.expandedTools[panelKey]);
  panel.addEventListener("toggle", () => setExpandedToolState(panelKey, panel.open));

  const panelSummary = document.createElement("summary");
  panelSummary.className = "tool-panel-summary";
  panelSummary.textContent = `工具调用 (${groups.size})`;
  panel.appendChild(panelSummary);

  const list = document.createElement("div");
  list.className = "tool-event-list";

  let toolIndex = 0;
  for (const [toolKey, tool] of groups.entries()) {
    const card = document.createElement("details");
    card.className = "tool-event";
    const detailKey = `${eventId}:tool:${toolKey || toolIndex}`;
    card.open = Boolean(state.ui.expandedTools[detailKey]);
    card.addEventListener("toggle", () => setExpandedToolState(detailKey, card.open));

    const summary = document.createElement("summary");
    summary.className = "tool-event-summary";
    summary.textContent = tool.name;
    card.appendChild(summary);

    const body = document.createElement("div");
    body.className = "tool-event-detail";

    if (tool.args !== null) {
      const argsTitle = document.createElement("p");
      argsTitle.className = "tool-event-label";
      argsTitle.textContent = "参数";
      const argsBody = document.createElement("pre");
      argsBody.className = "tool-event-body";
      argsBody.textContent = JSON.stringify(tool.args || {}, null, 2);
      body.append(argsTitle, argsBody);
    }

    if (tool.output !== null) {
      const outputTitle = document.createElement("p");
      outputTitle.className = "tool-event-label";
      outputTitle.textContent = "输出";
      const outputBody = document.createElement("pre");
      outputBody.className = "tool-event-body";
      outputBody.textContent = typeof tool.output === "string"
        ? (tool.output || "(无输出)")
        : JSON.stringify(tool.output || {}, null, 2);
      body.append(outputTitle, outputBody);
    }

    if (!body.childElementCount) {
      const empty = document.createElement("p");
      empty.className = "tool-event-label";
      empty.textContent = "暂无详细信息";
      body.appendChild(empty);
    }

    card.appendChild(body);
    list.appendChild(card);
    toolIndex += 1;
  }

  panel.appendChild(list);
  container.appendChild(panel);
}

function renderEvents(previousSnapshotKey) {
  if (isTimelineSelectionActive()) {
    return;
  }

  const events = [...state.events].sort((a, b) => a.created_at - b.created_at);
  const nextSnapshotKey = buildSnapshotKey(events);
  const shouldStick = state.ui.stickToBottom || previousSnapshotKey === "";

  timeline.innerHTML = "";
  heroEventCount.textContent = String(events.length);
  timelineCount.textContent = `${events.length} 条事件`;

  for (const event of events) {
    const node = eventTemplate.content.firstElementChild.cloneNode(true);
    const title = node.querySelector(".event-title");
    const kind = node.querySelector(".event-kind");
    const time = node.querySelector(".event-time");
    const meta = node.querySelector(".event-meta");
    const body = node.querySelector(".event-body");
    const tools = node.querySelector(".event-tools-list");

    kind.textContent = event.kind;
    title.textContent = event.agent_name === "User" ? "用户输入" : `@${event.agent_name}`;
    time.textContent = formatTime(event.created_at);

    const metaParts = [];
    if (event.sender) metaParts.push(`sender: ${event.sender}`);
    if (event.status) metaParts.push(`status: ${event.status}`);
    if (event.metadata?.depth !== undefined) metaParts.push(`depth: ${event.metadata.depth}`);
    meta.textContent = metaParts.join("  ·  ");
    body.innerHTML = renderMarkdown(event.text || "(无内容)");
    renderToolEvents(tools, event.metadata?.tools || [], event.id);

    renderMentions(node.querySelector(".mentions"), event.mentions || []);
    timeline.appendChild(node);
  }

  if (shouldStick) {
    timeline.scrollTop = timeline.scrollHeight;
  }

  state.ui.lastSnapshotKey = nextSnapshotKey;
}

function findMentionContext(value, caretIndex) {
  const uptoCaret = value.slice(0, caretIndex);
  const match = uptoCaret.match(/(^|\s)@([A-Za-z0-9_\-\u4e00-\u9fff]*)$/);
  if (!match) {
    return null;
  }

  return {
    start: caretIndex - match[2].length - 1,
    end: caretIndex,
    query: match[2] || "",
  };
}

function getMentionOptions(query) {
  const normalized = query.trim().toLowerCase();
  return state.agents
    .filter((agent) => !normalized || agent.name.toLowerCase().includes(normalized))
    .slice(0, 6);
}

function closeMentionMenu() {
  state.mention.open = false;
  state.mention.query = "";
  state.mention.start = -1;
  state.mention.end = -1;
  state.mention.activeIndex = 0;
  state.mention.options = [];
  mentionMenu.classList.add("hidden");
  mentionMenu.innerHTML = "";
  setComposerHint();
}

function renderMentionMenu() {
  const { open, options, activeIndex } = state.mention;
  mentionMenu.innerHTML = "";

  if (!open || options.length === 0) {
    mentionMenu.classList.add("hidden");
    setComposerHint();
    return;
  }

  for (const [index, agent] of options.entries()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mention-option";
    if (index === activeIndex) {
      button.classList.add("active");
      button.setAttribute("aria-selected", "true");
    }
    button.innerHTML = `<strong>@${agent.name}</strong><span>${agent.system_prompt || "点击插入到当前消息"}</span>`;
    button.addEventListener("mousedown", (event) => {
      event.preventDefault();
      insertMention(agent.name);
      messageInput.focus();
    });
    mentionMenu.appendChild(button);
  }

  mentionMenu.classList.remove("hidden");
  setComposerHint();
}

function updateMentionMenu() {
  const context = findMentionContext(messageInput.value, messageInput.selectionStart ?? 0);
  if (!context || state.agents.length === 0) {
    closeMentionMenu();
    return;
  }

  const options = getMentionOptions(context.query);
  if (options.length === 0) {
    closeMentionMenu();
    return;
  }

  state.mention.open = true;
  state.mention.query = context.query;
  state.mention.start = context.start;
  state.mention.end = context.end;
  state.mention.options = options;
  state.mention.activeIndex = Math.min(state.mention.activeIndex, options.length - 1);
  renderMentionMenu();
}

function insertMention(name) {
  const value = messageInput.value;
  const selectionStart = messageInput.selectionStart ?? value.length;
  const selectionEnd = messageInput.selectionEnd ?? value.length;
  const start = state.mention.start >= 0 ? state.mention.start : selectionStart;
  const end = state.mention.end >= 0 ? state.mention.end : selectionEnd;
  const prefix = value.slice(0, start);
  const suffix = value.slice(end);
  const replacement = `@${name}`;
  const needsTrailingSpace = suffix.length === 0 || !/^[\s，。,.!?]/.test(suffix);
  const nextValue = `${prefix}${replacement}${needsTrailingSpace ? " " : ""}${suffix}`;

  messageInput.value = nextValue;
  const caret = prefix.length + replacement.length + (needsTrailingSpace ? 1 : 0);
  messageInput.setSelectionRange(caret, caret);
  closeMentionMenu();
}

async function refresh() {
  const previousSnapshotKey = state.ui.lastSnapshotKey;
  const payload = await api("/api/state");
  state.session = payload.session || null;
  state.sessions = payload.sessions || [];
  state.workspaces = payload.workspaces || [];
  state.current_session_id = payload.current_session_id || "";
  state.agents = payload.agents || [];
  state.events = payload.events || [];
  state.acp = payload.acp || state.acp;
  renderSessions();
  renderAgents();
  renderEvents(previousSnapshotKey);
  renderAcpHints();
  renderWorkspaceOptions();
  updateMentionMenu();
}

sessionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(sessionForm);
  try {
    await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({
        title: form.get("title"),
        workspace_id: form.get("workspace_id"),
      }),
    });
    closeSessionModal();
    openSettings();
    state.ui.stickToBottom = true;
    await refresh();
  } catch (error) {
    window.alert(error.message);
  }
});

agentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(agentForm);
  try {
    await api("/api/agents", {
      method: "POST",
      body: JSON.stringify({
        name: form.get("name"),
        system_prompt: form.get("system_prompt"),
        transport: form.get("transport"),
        acp_agent_name: form.get("acp_agent_name"),
        acp_command: form.get("acp_command"),
        stdio_command: form.get("stdio_command"),
      }),
    });
    closeAgentModal();
    openSettings();
    await refresh();
  } catch (error) {
    window.alert(error.message);
  }
});

agentAcpCommandInput.addEventListener("blur", () => {
  refreshAgentAcpSuggestions().catch((error) => console.error(error));
});

agentAcpNameInput.addEventListener("blur", () => {
  if (!agentAcpCommandInput.value.trim()) {
    return;
  }
  refreshAgentAcpSuggestions().catch((error) => console.error(error));
});

agentTransportSelect.addEventListener("change", syncTransportFields);

messageForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/messages", {
      method: "POST",
      body: JSON.stringify({ text: messageInput.value }),
    });
    messageInput.value = "";
    state.ui.stickToBottom = true;
    closeMentionMenu();
    await refresh();
  } catch (error) {
    window.alert(error.message);
  }
});

stopDiscussionsButton.addEventListener("click", async () => {
  try {
    await api("/api/stop", {
      method: "POST",
      body: "{}",
    });
    await refresh();
  } catch (error) {
    window.alert(error.message);
  }
});

openSettingsButton.addEventListener("click", openSettings);
closeSettingsButton.addEventListener("click", closeSettings);
openSessionModalButton.addEventListener("click", openSessionModal);
closeSessionModalButton.addEventListener("click", closeSessionModal);
cancelSessionModalButton.addEventListener("click", closeSessionModal);
openAgentModalButton.addEventListener("click", openAgentModal);
closeAgentModalButton.addEventListener("click", closeAgentModal);
cancelAgentModalButton.addEventListener("click", closeAgentModal);
sessionWorkspaceSelect.addEventListener("change", updateWorkspaceHint);

settingsOverlay.addEventListener("click", () => {
  closeSessionModal();
  closeAgentModal();
  closeSettings();
});

timeline.addEventListener("scroll", () => {
  state.ui.stickToBottom = isNearBottom(timeline);
});

messageInput.addEventListener("input", () => {
  state.mention.activeIndex = 0;
  updateMentionMenu();
});

messageInput.addEventListener("click", updateMentionMenu);
messageInput.addEventListener("keyup", updateMentionMenu);

messageInput.addEventListener("keydown", (event) => {
  if (event.isComposing || event.keyCode === 229) {
    return;
  }

  if (event.key === "Enter" && !event.shiftKey && !state.mention.open) {
    event.preventDefault();
    if (messageInput.value.trim()) {
      messageForm.requestSubmit();
    }
    return;
  }

    if (!state.mention.open || state.mention.options.length === 0) {
      if (event.key === "Escape") {
        closeSessionModal();
        closeAgentModal();
        closeSettings();
      }
    return;
  }

  if (event.key === "ArrowDown") {
    event.preventDefault();
    state.mention.activeIndex = (state.mention.activeIndex + 1) % state.mention.options.length;
    renderMentionMenu();
    return;
  }

  if (event.key === "ArrowUp") {
    event.preventDefault();
    state.mention.activeIndex =
      (state.mention.activeIndex - 1 + state.mention.options.length) % state.mention.options.length;
    renderMentionMenu();
    return;
  }

  if (event.key === "Tab" || event.key === "Enter") {
    const context = findMentionContext(messageInput.value, messageInput.selectionStart ?? 0);
    if (!context) {
      return;
    }
    event.preventDefault();
    insertMention(state.mention.options[state.mention.activeIndex].name);
    return;
  }

  if (event.key === "Escape") {
    event.preventDefault();
    closeMentionMenu();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeMentionMenu();
    closeSessionModal();
    closeAgentModal();
    closeSettings();
  }
});

document.addEventListener("click", (event) => {
  if (!mentionMenu.contains(event.target) && event.target !== messageInput) {
    closeMentionMenu();
  }
});

refresh();
state.polling = window.setInterval(() => {
  refresh().catch((error) => console.error(error));
}, 1200);
