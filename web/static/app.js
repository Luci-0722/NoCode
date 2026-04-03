const state = {
  agents: [],
  events: [],
  acp: {
    base_url: "",
    default_agent_name: "",
    available_agents: [],
  },
  polling: null,
  ui: {
    settingsOpen: false,
    agentModalOpen: false,
    stickToBottom: true,
    lastSnapshotKey: "",
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
const composerHint = document.querySelector("#composer-hint");
const stopDiscussionsButton = document.querySelector("#stop-discussions");
const mentionMenu = document.querySelector("#mention-menu");
const messageInput = document.querySelector("#message-input");
const settingsOverlay = document.querySelector("#settings-overlay");
const settingsDrawer = document.querySelector("#settings-drawer");
const openSettingsButton = document.querySelector("#open-settings");
const closeSettingsButton = document.querySelector("#close-settings");
const openAgentModalButton = document.querySelector("#open-agent-modal");
const closeAgentModalButton = document.querySelector("#close-agent-modal");
const cancelAgentModalButton = document.querySelector("#cancel-agent-modal");
const agentModal = document.querySelector("#agent-modal");
const agentNameInput = document.querySelector("#agent-name");
const agentTransportSelect = document.querySelector("#agent-transport");
const agentAcpNameInput = document.querySelector("#agent-acp-name");
const agentAcpBaseUrlInput = document.querySelector("#agent-acp-base-url");
const agentAcpNameField = document.querySelector("#agent-acp-name-field");
const agentAcpBaseUrlField = document.querySelector("#agent-acp-base-url-field");
const agentStdioCommandField = document.querySelector("#agent-stdio-command-field");
const agentStdioCommandInput = document.querySelector("#agent-stdio-command");
const agentAcpHint = document.querySelector("#agent-acp-hint");
const acpAgentOptions = document.querySelector("#acp-agent-options");
const agentTemplate = document.querySelector("#agent-template");
const eventTemplate = document.querySelector("#event-template");

agentList.dataset.empty = "还没有 agent。点击右上角设置后新建角色。";
timeline.dataset.empty = "还没有协作事件。发送一条消息后，这里会显示完整的接力轨迹。";

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
  if (!state.ui.agentModalOpen) {
    settingsOverlay.classList.add("hidden");
    document.body.classList.remove("no-scroll");
  }
}

function openAgentModal() {
  state.ui.agentModalOpen = true;
  settingsOverlay.classList.remove("hidden");
  agentModal.classList.remove("hidden");
  document.body.classList.add("no-scroll");
  agentAcpBaseUrlInput.value = state.acp.base_url || "";
  agentAcpNameInput.value = state.acp.default_agent_name || "";
  agentTransportSelect.value = "http";
  agentStdioCommandInput.value = "python3 -m src.backend_stdio";
  syncTransportFields();
  renderAcpHints();
  window.setTimeout(() => agentNameInput.focus(), 20);
}

function closeAgentModal() {
  state.ui.agentModalOpen = false;
  agentModal.classList.add("hidden");
  agentForm.reset();
  if (!state.ui.settingsOpen) {
    settingsOverlay.classList.add("hidden");
    document.body.classList.remove("no-scroll");
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
    const transportLabel = `Transport: ${agent.transport || "http"}`;
    const acpLabel = agent.transport === "stdio"
      ? `STDIO: ${agent.stdio_command || "-"}`
      : (agent.acp_agent_name ? `ACP: ${agent.acp_agent_name} @ ${agent.acp_base_url}` : "");
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
  const transport = agentTransportSelect.value || "http";
  const availableAgents = state.acp.available_agents || [];
  fillDatalist(acpAgentOptions, availableAgents);

  if (transport === "stdio") {
    agentAcpHint.textContent = "STDIO 模式会通过本地命令启动 agent 进程并复用它的会话。";
    return;
  }

  if (state.acp.base_url) {
    const name = state.acp.default_agent_name || "未选择";
    const count = availableAgents.length;
    agentAcpHint.textContent = `最近一次探测：${state.acp.base_url}，默认 agent：${name}，发现 ${count} 个 manifest。`;
  } else {
    agentAcpHint.textContent = "填写这个 agent 自己要连接的 ACP 地址和本地 agent 名。";
  }
}

function syncTransportFields() {
  const transport = agentTransportSelect.value || "http";
  const stdio = transport === "stdio";
  agentAcpNameField.classList.toggle("hidden", stdio);
  agentAcpBaseUrlField.classList.toggle("hidden", stdio);
  agentStdioCommandField.classList.toggle("hidden", !stdio);
  renderAcpHints();
}

async function refreshAgentAcpSuggestions() {
  const baseUrl = agentAcpBaseUrlInput.value.trim();
  const agentName = agentAcpNameInput.value.trim();
  if (!baseUrl) {
    state.acp = { ...state.acp, base_url: "", available_agents: [] };
    renderAcpHints();
    return;
  }

  try {
    if ((agentTransportSelect.value || "http") !== "http") {
      return;
    }
    const result = await api("/api/acp", {
      method: "POST",
      body: JSON.stringify({
        base_url: baseUrl,
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

function renderEvents(previousSnapshotKey) {
  const events = [...state.events].sort((a, b) => a.created_at - b.created_at);
  const nextSnapshotKey = buildSnapshotKey(events);
  const shouldStick = state.ui.stickToBottom || hasRunningAgent() || previousSnapshotKey === "";

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

    kind.textContent = event.kind;
    title.textContent = event.agent_name === "User" ? "用户输入" : `@${event.agent_name}`;
    time.textContent = formatTime(event.created_at);

    const metaParts = [];
    if (event.sender) metaParts.push(`sender: ${event.sender}`);
    if (event.status) metaParts.push(`status: ${event.status}`);
    if (event.metadata?.depth !== undefined) metaParts.push(`depth: ${event.metadata.depth}`);
    meta.textContent = metaParts.join("  ·  ");
    body.textContent = event.text || "(无内容)";

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
  state.agents = payload.agents || [];
  state.events = payload.events || [];
  state.acp = payload.acp || state.acp;
  renderAgents();
  renderEvents(previousSnapshotKey);
  renderAcpHints();
  updateMentionMenu();
}

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
        acp_base_url: form.get("acp_base_url"),
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

agentAcpBaseUrlInput.addEventListener("blur", () => {
  refreshAgentAcpSuggestions().catch((error) => console.error(error));
});

agentAcpNameInput.addEventListener("blur", () => {
  if (!agentAcpBaseUrlInput.value.trim()) {
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
openAgentModalButton.addEventListener("click", openAgentModal);
closeAgentModalButton.addEventListener("click", closeAgentModal);
cancelAgentModalButton.addEventListener("click", closeAgentModal);

settingsOverlay.addEventListener("click", () => {
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
  if (!state.mention.open || state.mention.options.length === 0) {
    if (event.key === "Escape") {
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
