"use strict";

const elements = Object.fromEntries([
  "status", "account", "connect", "disconnect", "refresh", "name", "create",
  "rooms", "room-summary", "match-summary", "members", "camp", "ready", "start",
  "leave", "loaded", "phase-help", "view", "events"
].map(id => [id, document.getElementById(id)]));
const phases = { waiting: "等待准备", loading: "等待加载", countdown: "统一倒计时", running: "房间流程运行中" };
const errors = {
  accountConnected: "该测试身份已有连接，请更换身份。",
  invalidAccountId: "身份只能使用 1–36 个英文字母、数字、下划线或连字符。",
  alreadyInRoom: "你已经加入房间。", roomFull: "房间已满。", roomNotFound: "房间已关闭，请刷新。",
  notReady: "其他玩家尚未准备。", invalidTeams: "两人需要选择不同阵营。",
  notOwner: "只有房主可以开局。", wrongPhase: "当前房间阶段不允许此操作。",
  staleMatch: "对局已经变化，请以最新房间状态为准。", notInRoom: "你尚未加入房间。",
  invalidRoomName: "房间名需要 1–32 个字符，不能包含控制字符。", roomLimit: "房间数量已达上限。"
};
let socket = null;
let identified = false;
let account = "";
let sequence = 0;
let room = null;
let rooms = [];
let view = null;
let heartbeat = null;
const pending = new Map();
const events = [];

function status(text) { elements.status.textContent = text; }
function log(direction, data) {
  events.push(`${direction} ${JSON.stringify(data)}`);
  if (events.length > 60) events.shift();
  elements.events.textContent = events.join("\n");
  elements.events.scrollTop = elements.events.scrollHeight;
}

function send(type, payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN || pending.size >= 8) return;
  const requestId = String(++sequence);
  const command = payload === undefined ? { type } : { type, payload };
  const request = { protocolVersion: 1, requestId, command };
  pending.set(requestId, { type, sentAt: Date.now() });
  socket.send(JSON.stringify(request));
  log("→", request);
  render();
}

function table(container, headings, rows) {
  container.replaceChildren();
  const result = document.createElement("table");
  const head = result.createTHead().insertRow();
  for (const heading of headings) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = heading;
    head.append(cell);
  }
  const body = result.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const value of row) tr.insertCell().textContent = String(value);
  }
  container.append(result);
}

function render() {
  const connected = socket !== null;
  const busy = [...pending.values()].some(request => !["ping", "getClientView"].includes(request.type));
  const available = identified && !busy;
  const local = room?.members.find(member => member.accountId === account);
  const waiting = room?.phase === "waiting";
  elements.account.disabled = connected;
  elements.connect.disabled = connected;
  elements.disconnect.disabled = !connected;
  elements.refresh.disabled = !available;
  elements.create.disabled = !available || room !== null;
  elements.name.disabled = !available || room !== null;
  elements.leave.disabled = !available || room === null;
  elements.camp.disabled = !available || !waiting;
  const owner = room?.ownerAccountId === account;
  elements.ready.disabled = !available || !waiting || owner;
  elements.ready.textContent = owner ? "房主无需准备" : local?.ready ? "取消准备" : "准备";
  if (local) elements.camp.value = String(local.camp);
  elements.start.disabled = !available || !waiting || room.ownerAccountId !== account
    || room.members.some(member => member.accountId !== account && !member.ready)
    || (room.members.length > 1 && room.members.every(member => member.camp === room.members[0].camp));
  elements.loaded.disabled = !available || room?.phase !== "loading" || local?.loaded;

  elements.rooms.replaceChildren();
  if (rooms.length === 0) elements.rooms.textContent = identified ? "暂无房间，创建一个或点击刷新。" : "连接后刷新房间列表。";
  for (const entry of rooms) {
    const row = document.createElement("div");
    row.className = "room-row";
    const text = document.createElement("span");
    text.textContent = `${entry.name} · ${entry.members.length}/${entry.capacity} · ${phases[entry.phase]}`;
    const join = document.createElement("button");
    join.type = "button";
    join.textContent = "加入";
    join.disabled = !available || room !== null || entry.phase !== "waiting" || entry.members.length >= entry.capacity;
    join.addEventListener("click", () => send("joinRoom", { roomId: entry.roomId }));
    row.append(text, join);
    elements.rooms.append(row);
  }
  elements["room-summary"].textContent = room ? `${room.name} · ${phases[room.phase]} · 房主 ${room.ownerAccountId}` : "尚未加入房间";
  elements["match-summary"].textContent = room ? `roomId: ${room.roomId}${room.currentMatch ? ` · matchId: ${room.currentMatch.matchId}` : ""}` : "";
  if (room) table(elements.members, ["成员", "阵营", "准备", "加载"], room.members.map(member => [
    member.accountId === account ? `${member.accountId}（我）` : member.accountId,
    member.camp, member.accountId === room.ownerAccountId ? "房主无需准备" : member.ready ? "已准备" : "未准备", member.loaded ? "已确认" : "未确认"
  ]));
  else elements.members.replaceChildren();
  const help = {
    loading: "此处只能模拟加载确认。真实加载回报尚未接入，点击按钮不会启动游戏。",
    countdown: "本局成员已确认加载，等待服务端统一开始。",
    running: "房间开局流程完成；原版游戏接入、移动与战斗尚未实现。"
  };
  elements["phase-help"].textContent = help[room?.phase]
    || (room?.lastAbortReason ? `上次开局已取消：${room.lastAbortReason}。可重新加入、准备和开局。` : "房主直接开局，可单人进入；有其他玩家时需等待其准备，双人选择不同阵营。");
  if (view) table(elements.view, ["房间角色编号", "本端编号（RID）", "阵营", "归属"], view.actors.map(actor => [
    actor.actorId, actor.localRid, actor.camp, actor.isLocal ? "自己" : "对方"
  ]));
  else elements.view.textContent = "等待开局";
}

function applyRoom(next) {
  if (room?.roomId === next.roomId && next.revision < room.revision) return;
  room = next;
  const listed = rooms.findIndex(entry => entry.roomId === next.roomId);
  if (listed === -1) rooms.push(next);
  else rooms[listed] = next;
  if (view?.matchId !== room.currentMatch?.matchId) view = null;
  // Request the view once per match. Loading flags and countdown updates do
  // not change its identity bindings; the room snapshot displays those stages.
  if (room.currentMatch && !view && ![...pending.values()].some(request => request.type === "getClientView")) {
    send("getClientView", { matchId: room.currentMatch.matchId });
  }
}

function receive(data) {
  let response;
  try { response = JSON.parse(data); } catch { status("收到无法解析的回复，连接已关闭。"); disconnect(); return; }
  log("←", response);
  if (response.protocolVersion !== 1) { status("服务端协议版本不兼容。"); disconnect(); return; }
  if (response.requestId) pending.delete(response.requestId);
  const { type, payload } = response.event;
  if (type === "welcome") {
    identified = true;
    account = payload.accountId;
    status(`已连接：${account}`);
    send("listRooms");
  } else if (type === "roomList") rooms = payload.rooms;
  else if (type === "room" || type === "roomChanged") applyRoom(payload.room);
  else if (type === "clientView" && payload.view.matchId === room?.currentMatch?.matchId) view = payload.view;
  else if (type === "left") {
    room = null;
    view = null;
    send("listRooms");
  } else if (type === "error") status(errors[payload.code] || `服务端拒绝请求：${payload.code}`);
  render();
}

function disconnect() {
  const previous = socket;
  socket = null;
  identified = false;
  room = null;
  rooms = [];
  view = null;
  pending.clear();
  clearInterval(heartbeat);
  heartbeat = null;
  previous?.close();
  render();
}

elements.connect.addEventListener("click", () => {
  const label = elements.account.value.trim();
  if (!/^[A-Za-z0-9_-]{1,36}$/.test(label)) { status(errors.invalidAccountId); return; }
  sequence = 0;
  events.length = 0;
  elements.events.textContent = "";
  const current = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
  socket = current;
  status("正在连接…");
  current.addEventListener("open", () => {
    if (socket !== current) return;
    send("hello", { accountId: label });
    heartbeat = setInterval(() => {
      if ([...pending.values()].some(request => Date.now() - request.sentAt > 10000)) {
        status("请求超时，连接已关闭。重新连接后加入房间。");
        disconnect();
      } else if (identified) send("ping");
    }, 5000);
  });
  current.addEventListener("message", event => { if (socket === current) receive(event.data); });
  current.addEventListener("close", () => {
    if (socket !== current) return;
    status("连接已关闭；房间归属已清理，可重新连接。");
    disconnect();
  });
  current.addEventListener("error", () => {
    if (socket !== current) return;
    status("连接失败，请确认服务使用 --dev-lobby 启动。");
    disconnect();
  });
  render();
});
elements.disconnect.addEventListener("click", () => { status("已断开，正在退出房间。"); disconnect(); });
elements.refresh.addEventListener("click", () => send("listRooms"));
elements.create.addEventListener("click", () => send("createRoom", { name: elements.name.value, mapId: 1028 }));
elements.camp.addEventListener("change", () => send("setCamp", { camp: Number(elements.camp.value) }));
elements.ready.addEventListener("click", () => send("ready", { ready: !room.members.find(member => member.accountId === account).ready }));
elements.start.addEventListener("click", () => send("start"));
elements.leave.addEventListener("click", () => send("leaveRoom"));
elements.loaded.addEventListener("click", () => send("loaded", { matchId: room.currentMatch.matchId }));
window.addEventListener("pagehide", disconnect);
render();
