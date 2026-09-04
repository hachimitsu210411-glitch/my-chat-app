import json
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

rooms_db: dict[str, dict] = {}
room_history: dict[str, list[dict]] = {}

# フィードバックを保存するためのリスト（サーバー再起動で消えます）
feedback_list: list[dict] = []

ROOM_EXPIRATION_SECONDS = 2 * 3600

def cleanup_expired_rooms():
    current_time = time.time()
    expired = [name for name, info in rooms_db.items() if current_time - info["last_activity"] > ROOM_EXPIRATION_SECONDS]
    for name in expired:
        del rooms_db[name]
        if name in room_history:
            del room_history[name]

def update_room_activity(room_name: str):
    if room_name in rooms_db:
        rooms_db[room_name]["last_activity"] = time.time()

class ConnectionManager:
    def __init__(self):
        self.rooms: dict[str, list[WebSocket]] = {}

    async def connect(self, room_name: str, websocket: WebSocket):
        await websocket.accept()
        if room_name not in self.rooms:
            self.rooms[room_name] = []
        self.rooms[room_name].append(websocket)

    def disconnect(self, room_name: str, websocket: WebSocket):
        if room_name in self.rooms:
            if websocket in self.rooms[room_name]:
                self.rooms[room_name].remove(websocket)
            if not self.rooms[room_name]:
                del self.rooms[room_name]

    async def broadcast(self, room_name: str, data: dict, save_history: bool = True):
        json_data = json.dumps(data)
        if save_history:
            if room_name not in room_history:
                room_history[room_name] = []
            room_history[room_name].append(data)
            if len(room_history[room_name]) > 50:
                room_history[room_name].pop(0)

        if room_name in self.rooms:
            disconnected_sockets = []
            for connection in list(self.rooms[room_name]):
                try:
                    await connection.send_text(json_data)
                except Exception:
                    disconnected_sockets.append(connection)
            
            for dead_socket in disconnected_sockets:
                self.disconnect(room_name, dead_socket)

manager = ConnectionManager()

class RoomCreate(BaseModel):
    name: str
    tags: list[str] = []
    icon: str = ""

class FeedbackCreate(BaseModel):
    name: str
    message: str

@app.get("/api/rooms")
async def get_rooms():
    cleanup_expired_rooms()
    return [{"name": name, "tags": info["tags"], "icon": info.get("icon", "")} for name, info in rooms_db.items()]

@app.post("/api/rooms")
async def create_room(room: RoomCreate):
    cleanup_expired_rooms()
    room_name = room.name.strip()
    if room_name:
        rooms_db[room_name] = {
            "tags": [t.strip() for t in room.tags if t.strip()],
            "icon": room.icon,
            "last_activity": time.time()
        }
    return await get_rooms()

# フィードバックを受け取るエンドポイント
@app.post("/api/feedback")
async def submit_feedback(feedback: FeedbackCreate):
    feedback_data = {
        "name": feedback.name,
        "message": feedback.message,
        "time": time.time()
    }
    feedback_list.append(feedback_data)
    # コンソール画面にも表示させる
    print(f"\n[フィードバック受信] 送信者: {feedback.name}\n内容: {feedback.message}\n")
    return {"status": "success"}

html_content = """
<!DOCTYPE html>
<html lang="ja">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>趣味マッチングチャット</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: #eef2f5;
                display: flex;
                justify-content: center;
                height: 100dvh;
                overflow: hidden;
            }

            .app-container {
                width: 100%;
                max-width: 600px;
                height: 100dvh;
                display: flex;
                flex-direction: column;
                background-color: #ffffff;
                box-shadow: 0 0 10px rgba(0,0,0,0.1);
                position: relative;
            }

            .navbar {
                background: #333;
                color: white;
                padding: 10px 15px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                flex-shrink: 0;
            }
            .home-btn { background: #f0f0f0; color: #333; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; }

            .screen { flex: 1; overflow-y: auto; padding: 15px; display: flex; flex-direction: column; }
            .hidden { display: none !important; }

            .room-card { margin: 8px 0; padding: 10px; border: 1px solid #ccc; border-radius: 4px; background: #fafafa; display: flex; align-items: center; gap: 12px; }
            .tag { display: inline-block; background: #e0e0e0; font-size: 11px; padding: 4px 8px; margin-right: 4px; border-radius: 3px; }
            .recommend-tag { background: #ffe082; font-weight: bold; }
            .create-box { margin-top: 15px; padding: 15px; border: 1px solid #aaa; background: #fdfdfd; border-radius: 4px; }
            
            .icon-avatar { width: 36px; height: 36px; border-radius: 4px; object-fit: cover; background: #ddd; cursor: pointer; flex-shrink: 0; }
            .room-icon-img { width: 48px; height: 48px; border-radius: 4px; object-fit: cover; background: #eee; flex-shrink: 0; }
            .preview-img { width: 60px; height: 60px; border-radius: 4px; object-fit: cover; display: block; margin-top: 5px; }

            #chatScreen { padding: 0; overflow: hidden; display: flex; flex-direction: column; background: #ffffff; }
            .chat-header { padding: 10px 15px; background: #f9f9f9; border-bottom: 1px solid #ddd; display: flex; justify-content: space-between; align-items: center; flex-shrink: 0; }
            .chat-header h2 { font-size: 16px; margin: 0; color: #333; }
            .exit-btn { background: #fff; color: #d9534f; border: 1px solid #d9534f; padding: 4px 10px; border-radius: 4px; cursor: pointer; }

            .chat-messages { flex: 1; overflow-y: auto; padding: 15px; list-style: none; display: flex; flex-direction: column; gap: 15px; }
            .chat-item { display: flex; align-items: flex-start; gap: 10px; border-bottom: 1px dashed #eee; padding-bottom: 10px; }
            .chat-content { display: flex; flex-direction: column; width: 100%; }
            .chat-name { font-size: 12px; color: #555; margin-bottom: 4px; cursor: pointer; font-weight: bold; }
            .chat-text { font-size: 14px; color: #222; word-break: break-all; line-height: 1.4; }
            .chat-send-img { max-width: 200px; max-height: 200px; border: 1px solid #ccc; margin-top: 4px; }
            .system-msg { color: #888; text-align: center; font-size: 12px; margin: 10px 0; font-style: italic; }

            .chat-input-bar { padding: 10px 15px; background: #f5f5f5; border-top: 1px solid #ddd; display: flex; gap: 8px; align-items: center; flex-shrink: 0; }
            .chat-input-bar input[type="text"] { flex: 1; padding: 8px; border: 1px solid #aaa; border-radius: 4px; outline: none; font-size: 14px; }
            .chat-input-bar button { padding: 8px 12px; background-color: #333; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; white-space: nowrap; }
            .btn-img-select { background-color: #666 !important; }

            .modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: flex; justify-content: center; align-items: center; z-index: 1000; }
            .modal-content { background: white; padding: 20px; border-radius: 6px; width: 280px; text-align: center; position: relative; }
            .modal-avatar { width: 70px; height: 70px; border-radius: 4px; object-fit: cover; margin-bottom: 10px; border: 1px solid #ddd; }
            .modal-bio { background: #f9f9f9; padding: 10px; border: 1px solid #eee; font-size: 13px; margin: 10px 0; color: #444; }

            input[type="text"] { padding: 6px; border: 1px solid #ccc; border-radius: 4px; }
            button { cursor: pointer; }
            
            .tag-remove { color: #d9534f; cursor: pointer; margin-left: 6px; font-weight: bold; font-size: 12px; }
            .tag-list-container { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 15px; }

            textarea { padding: 8px; border: 1px solid #ccc; border-radius: 4px; resize: vertical; font-family: inherit; }
        </style>
    </head>
    <body>

        <div class="app-container">
            <div class="navbar">
                <span>学校趣味チャット</span>
                <button class="home-btn" onclick="goHome()">ホームに戻る</button>
            </div>

            <!-- 1. プロフィール設定画面 -->
            <div id="profileScreen" class="screen">
                <h2>プロフィール登録</h2>
                <br>
                <p>名前:</p>
                <input type="text" id="usernameInput" placeholder="例: たろう" style="width: 100%; margin-bottom: 10px;" />
                
                <p>一言コメント:</p>
                <input type="text" id="userBioInput" placeholder="例: よろしくお願いします！" style="width: 100%; margin-bottom: 10px;" />

                <p>アイコン画像を選択:</p>
                <input type="file" id="userIconInput" accept="image/*" onchange="previewUserIcon(event)" style="margin-bottom: 10px;" />
                <img id="userIconPreview" class="preview-img hidden" />

                <p style="margin-top: 10px;">定番ジャンル（選択）:</p>
                <div style="display: flex; gap: 8px; flex-wrap: wrap; margin: 5px 0 10px 0;">
                    <label><input type="checkbox" class="hobby-check" value="アニメ" /> アニメ</label>
                    <label><input type="checkbox" class="hobby-check" value="ボカロ" /> ボカロ</label>
                    <label><input type="checkbox" class="hobby-check" value="軽音" /> 軽音</label>
                    <label><input type="checkbox" class="hobby-check" value="ゲーム" /> ゲーム</label>
                    <label><input type="checkbox" class="hobby-check" value="声優" /> 声優</label>
                </div>
                
                <p>自由に追加する趣味・タグ:</p>
                <div style="display: flex; gap: 6px; margin-bottom: 8px;">
                    <input type="text" id="customHobbyInput" placeholder="例: イラスト" style="flex: 1;" onkeydown="if(event.key==='Enter') addCustomHobby()" />
                    <button onclick="addCustomHobby()" style="padding: 6px 12px; background: #666; color: white; border: none; border-radius: 4px;">追加</button>
                </div>
                <div id="customHobbyList" class="tag-list-container"></div>
                
                <button onclick="saveProfile()" style="margin-top: 5px; padding: 10px; background: #333; color: white; border: none; border-radius: 4px;">保存してロビーへ</button>
            </div>

            <!-- 2. ロビー画面 -->
            <div id="lobbyScreen" class="screen hidden">
                <h2 id="welcomeText">ようこそ！</h2>
                
                <div style="color: #d97706; margin-top: 10px;">
                    <h3>★ おすすめのルーム:</h3>
                    <div id="recommendedRoomList">該当するルームがありません</div>
                </div>

                <hr style="margin: 15px 0; border: 0; border-top: 1px solid #ddd;">

                <h3>すべてのルーム一覧:</h3>
                <div id="allRoomList">まだ部屋がありません。</div>

                <div class="create-box">
                    <h4>新しい趣味ルームを作る</h4>
                    <p style="margin-top: 5px;">ルーム名:</p>
                    <input type="text" id="newRoomNameInput" placeholder="例: 競プロ部屋" style="width: 100%; margin-bottom: 5px;" />
                    
                    <p style="margin-top: 5px;">アイコン:</p>
                    <input type="file" id="newRoomIconInput" accept="image/*" style="margin-bottom: 5px;" />
                    
                    <p style="margin-top: 5px;">タグ追加:</p>
                    <div style="display: flex; gap: 6px; margin-bottom: 8px;">
                        <input type="text" id="newRoomTagInput" placeholder="例: 競プロ" style="flex: 1;" onkeydown="if(event.key==='Enter') addRoomTag()" />
                        <button onclick="addRoomTag()" style="padding: 6px 12px; background: #666; color: white; border: none; border-radius: 4px;">追加</button>
                    </div>
                    <div id="newRoomTagsList" class="tag-list-container"></div>

                    <button onclick="createNewRoom()" style="margin-top: 5px; padding: 6px 12px; background: #333; color: white; border: none; border-radius: 4px;">ルームを作成</button>
                </div>

                <br>
                <button onclick="backToProfile()" style="padding: 8px; border: 1px solid #ccc; background: #fff; border-radius: 4px;">プロフィール変更</button>

                <!-- フィードバック機能 -->
                <hr style="margin: 30px 0 15px 0; border: 0; border-top: 1px solid #ddd;">
                <div class="create-box" style="background: #f4f6f8; border-color: #d0d7de; margin-bottom: 30px;">
                    <h4 style="color: #333;">意見・要望を送る</h4>
                    <p style="margin-top: 5px; font-size: 12px; color: #666;">「こんな機能が欲しい！」「ここが使いにくい」など教えてください。</p>
                    <textarea id="feedbackInput" rows="3" placeholder="ここに入力..." style="width: 100%; margin: 8px 0;"></textarea>
                    <button onclick="sendFeedback()" style="padding: 6px 12px; background: #28a745; color: white; border: none; border-radius: 4px;">運営に送信する</button>
                </div>
            </div>

            <!-- 3. チャット画面 -->
            <div id="chatScreen" class="screen hidden">
                <div class="chat-header">
                    <h2 id="currentRoomTitle">チャットルーム</h2>
                    <button class="exit-btn" onclick="goHome()">退室する</button>
                </div>

                <ul id="messages" class="chat-messages"></ul>

                <div class="chat-input-bar">
                    <button class="btn-img-select" onclick="document.getElementById('chatImageInput').click()">画像</button>
                    <input type="file" id="chatImageInput" accept="image/*" class="hidden" onchange="sendImageMessage(event)" />
                    <input type="text" id="messageText" placeholder="メッセージを入力..." autocomplete="off" onkeydown="handleKeyDown(event)" />
                    <button onclick="sendMessage()">送信</button>
                </div>
            </div>

            <!-- 4. 相手プロフィール確認用モーダル -->
            <div id="profileModal" class="modal-overlay hidden">
                <div class="modal-content">
                    <img id="modalAvatar" class="modal-avatar" src="" />
                    <h3 id="modalName" style="margin: 5px 0;"></h3>
                    <div id="modalBio" class="modal-bio"></div>
                    <div id="modalTags" style="margin-bottom: 15px;"></div>
                    <button onclick="closeProfileModal()" style="padding: 6px 16px; background: #333; color: white; border: none; border-radius: 4px;">閉じる</button>
                </div>
            </div>
        </div>

        <script>
            var currentUser = "";
            var userBio = "";
            var userIconBase64 = "";
            var userHobbies = [];
            
            var customHobbiesArray = [];
            var newRoomTagsArray = [];
            
            var ws = null;

            window.onload = function() {
                var savedName = localStorage.getItem("chat_username");
                var savedBio = localStorage.getItem("chat_user_bio");
                var savedHobbies = localStorage.getItem("chat_hobbies");
                var savedIcon = localStorage.getItem("chat_user_icon");

                if (savedName) document.getElementById("usernameInput").value = savedName;
                if (savedBio) document.getElementById("userBioInput").value = savedBio;
                if (savedIcon) {
                    userIconBase64 = savedIcon;
                    var preview = document.getElementById("userIconPreview");
                    preview.src = savedIcon;
                    preview.classList.remove("hidden");
                }
                
                if (savedHobbies) {
                    userHobbies = JSON.parse(savedHobbies);
                    var checkboxes = document.querySelectorAll('.hobby-check');
                    customHobbiesArray = [];

                    userHobbies.forEach(function(hobby) {
                        var found = false;
                        checkboxes.forEach(function(cb) {
                            if (cb.value === hobby) {
                                cb.checked = true;
                                found = true;
                            }
                        });
                        if (!found) customHobbiesArray.push(hobby);
                    });
                    renderCustomHobbies();
                }
            };

            window.addEventListener("beforeunload", function() {
                if (ws) {
                    ws.close();
                }
            });

            function addCustomHobby() {
                var input = document.getElementById("customHobbyInput");
                var val = input.value.trim();
                if (val && !customHobbiesArray.includes(val)) {
                    customHobbiesArray.push(val);
                    renderCustomHobbies();
                }
                input.value = "";
            }

            function removeCustomHobby(index) {
                customHobbiesArray.splice(index, 1);
                renderCustomHobbies();
            }

            function renderCustomHobbies() {
                var list = document.getElementById("customHobbyList");
                list.innerHTML = "";
                customHobbiesArray.forEach(function(tag, index) {
                    var span = document.createElement("span");
                    span.className = "tag";
                    span.innerHTML = "#" + tag + ' <span class="tag-remove" onclick="removeCustomHobby(' + index + ')">×</span>';
                    list.appendChild(span);
                });
            }

            function addRoomTag() {
                var input = document.getElementById("newRoomTagInput");
                var val = input.value.trim();
                if (val && !newRoomTagsArray.includes(val)) {
                    newRoomTagsArray.push(val);
                    renderRoomTags();
                }
                input.value = "";
            }

            function removeRoomTag(index) {
                newRoomTagsArray.splice(index, 1);
                renderRoomTags();
            }

            function renderRoomTags() {
                var list = document.getElementById("newRoomTagsList");
                list.innerHTML = "";
                newRoomTagsArray.forEach(function(tag, index) {
                    var span = document.createElement("span");
                    span.className = "tag";
                    span.innerHTML = "#" + tag + ' <span class="tag-remove" onclick="removeRoomTag(' + index + ')">×</span>';
                    list.appendChild(span);
                });
            }

            function scrollToBottom() {
                var messagesDiv = document.getElementById("messages");
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }

            function handleKeyDown(event) {
                if (event.key === "Enter" && !event.isComposing) {
                    sendMessage();
                }
            }

            function fileToBase64(file) {
                return new Promise(function(resolve, reject) {
                    if (!file) resolve("");
                    var reader = new FileReader();
                    reader.onload = function() { resolve(reader.result); };
                    reader.onerror = function() { resolve(""); };
                    reader.readAsDataURL(file);
                });
            }

            async function previewUserIcon(event) {
                var file = event.target.files[0];
                if (file) {
                    userIconBase64 = await fileToBase64(file);
                    var preview = document.getElementById("userIconPreview");
                    preview.src = userIconBase64;
                    preview.classList.remove("hidden");
                }
            }

            function goHome() {
                if (ws) {
                    ws.close();
                    ws = null;
                }
                if (currentUser) {
                    showScreen("lobbyScreen");
                    loadRooms();
                } else {
                    showScreen("profileScreen");
                }
            }

            async function saveProfile() {
                var nameInput = document.getElementById("usernameInput").value.trim();
                if (!nameInput) {
                    alert("名前を入力してください");
                    return;
                }
                currentUser = nameInput;
                userBio = document.getElementById("userBioInput").value.trim();

                localStorage.setItem("chat_username", currentUser);
                localStorage.setItem("chat_user_bio", userBio);
                localStorage.setItem("chat_user_icon", userIconBase64);

                userHobbies = [];
                document.querySelectorAll('.hobby-check:checked').forEach(function(cb) {
                    userHobbies.push(cb.value);
                });

                customHobbiesArray.forEach(function(tag) {
                    if (!userHobbies.includes(tag)) userHobbies.push(tag);
                });

                localStorage.setItem("chat_hobbies", JSON.stringify(userHobbies));
                document.getElementById("welcomeText").textContent = currentUser + " さんのマイロビー";
                goHome();
            }

            async function loadRooms() {
                var response = await fetch("/api/rooms");
                var rooms = await response.json();
                
                var recommendedList = document.getElementById("recommendedRoomList");
                var allList = document.getElementById("allRoomList");
                
                recommendedList.innerHTML = "";
                allList.innerHTML = "";

                if (rooms.length === 0) {
                    allList.textContent = "まだ部屋がありません。";
                    recommendedList.textContent = "部屋が存在しません。";
                    return;
                }

                var hasRecommend = false;

                rooms.forEach(function(room) {
                    var isMatched = room.tags.some(function(tag) {
                        return userHobbies.includes(tag);
                    });

                    if (isMatched) {
                        recommendedList.appendChild(createRoomCard(room));
                        hasRecommend = true;
                    }
                    
                    allList.appendChild(createRoomCard(room));
                });

                if (!hasRecommend) {
                    recommendedList.textContent = "おすすめの部屋がまだありません。";
                }
            }

            function createRoomCard(room) {
                var div = document.createElement("div");
                div.className = "room-card";

                var img = document.createElement("img");
                img.className = "room-icon-img";
                img.src = room.icon || "https://via.placeholder.com/48?text=Room";
                div.appendChild(img);

                var infoDiv = document.createElement("div");
                infoDiv.style.flex = "1";

                var title = document.createElement("strong");
                title.textContent = room.name;
                infoDiv.appendChild(title);

                var tagsDiv = document.createElement("div");
                tagsDiv.style.marginTop = "4px";

                room.tags.forEach(function(tag) {
                    var span = document.createElement("span");
                    span.className = "tag" + (userHobbies.includes(tag) ? " recommend-tag" : "");
                    span.textContent = "#" + tag;
                    tagsDiv.appendChild(span);
                });
                infoDiv.appendChild(tagsDiv);

                div.appendChild(infoDiv);

                var enterBtn = document.createElement("button");
                enterBtn.textContent = "入室する";
                enterBtn.style.padding = "4px 8px";
                enterBtn.style.background = "#333";
                enterBtn.style.color = "white";
                enterBtn.style.border = "none";
                enterBtn.style.borderRadius = "4px";
                enterBtn.onclick = function() { enterRoom(room.name); };
                div.appendChild(enterBtn);

                return div;
            }

            async function createNewRoom() {
                var nameInput = document.getElementById("newRoomNameInput");
                var iconInput = document.getElementById("newRoomIconInput");
                var roomName = nameInput.value.trim();

                if (!roomName) {
                    alert("ルーム名を入力してください");
                    return;
                }

                var roomIconBase64 = "";
                if (iconInput.files && iconInput.files[0]) {
                    roomIconBase64 = await fileToBase64(iconInput.files[0]);
                }

                var tags = newRoomTagsArray.slice();

                await fetch("/api/rooms", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: roomName, tags: tags, icon: roomIconBase64 })
                });

                nameInput.value = "";
                iconInput.value = "";
                newRoomTagsArray = [];
                renderRoomTags();
                
                loadRooms();
            }

            // --- 新しく追加したフィードバック送信機能 ---
            async function sendFeedback() {
                var input = document.getElementById("feedbackInput");
                var message = input.value.trim();
                
                if (!message) {
                    alert("メッセージを入力してください！");
                    return;
                }
                
                try {
                    await fetch("/api/feedback", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ name: currentUser || "未登録ユーザー", message: message })
                    });
                    
                    alert("フィードバックを送信しました。ご意見ありがとうございます！");
                    input.value = ""; // 送信後にテキストボックスを空にする
                } catch (e) {
                    alert("送信に失敗しました。時間をおいて再度お試しください。");
                }
            }
            // ------------------------------------------

            function enterRoom(roomName) {
                document.getElementById("currentRoomTitle").textContent = "「" + roomName + "」";
                document.getElementById("messages").innerHTML = "";

                var protocol = window.location.protocol === "https:" ? "wss://" : "ws://";
                ws = new WebSocket(protocol + window.location.host + "/ws/" + encodeURIComponent(roomName));

                ws.onmessage = function(event) {
                    var data = JSON.parse(event.data);
                    renderMessage(data);
                };

                showScreen("chatScreen");
            }

            function renderMessage(data) {
                var messages = document.getElementById('messages');
                
                if (data.type === "system") {
                    var sysDiv = document.createElement("div");
                    sysDiv.className = "system-msg";
                    sysDiv.textContent = "- " + data.message + " -";
                    messages.appendChild(sysDiv);
                } else {
                    var li = document.createElement("li");
                    li.className = "chat-item";

                    var img = document.createElement("img");
                    img.className = "icon-avatar";
                    img.src = data.icon || "https://via.placeholder.com/36?text=User";
                    img.onclick = function() { openProfileModal(data); };

                    var contentDiv = document.createElement("div");
                    contentDiv.className = "chat-content";

                    var nameDiv = document.createElement("div");
                    nameDiv.className = "chat-name";
                    nameDiv.textContent = data.name;
                    nameDiv.onclick = function() { openProfileModal(data); };

                    contentDiv.appendChild(nameDiv);

                    if (data.msgType === "image") {
                        var chatImg = document.createElement("img");
                        chatImg.className = "chat-send-img";
                        chatImg.src = data.message;
                        chatImg.onload = scrollToBottom;
                        contentDiv.appendChild(chatImg);
                    } else {
                        var textDiv = document.createElement("div");
                        textDiv.className = "chat-text";
                        textDiv.textContent = data.message;
                        contentDiv.appendChild(textDiv);
                    }

                    li.appendChild(img);
                    li.appendChild(contentDiv);
                    messages.appendChild(li);
                }
                scrollToBottom();
            }

            function sendMessage() {
                var input = document.getElementById("messageText");
                if (input.value.trim() !== "" && ws) {
                    var payload = {
                        type: "chat",
                        msgType: "text",
                        name: currentUser,
                        bio: userBio,
                        icon: userIconBase64,
                        tags: userHobbies,
                        message: input.value
                    };
                    ws.send(JSON.stringify(payload));
                    input.value = '';
                }
            }

            async function sendImageMessage(event) {
                var file = event.target.files[0];
                if (file && ws) {
                    var imgBase64 = await fileToBase64(file);
                    var payload = {
                        type: "chat",
                        msgType: "image",
                        name: currentUser,
                        bio: userBio,
                        icon: userIconBase64,
                        tags: userHobbies,
                        message: imgBase64
                    };
                    ws.send(JSON.stringify(payload));
                    event.target.value = "";
                }
            }

            function openProfileModal(userData) {
                document.getElementById("modalAvatar").src = userData.icon || "https://via.placeholder.com/70?text=User";
                document.getElementById("modalName").textContent = userData.name || "名無し";
                document.getElementById("modalBio").textContent = userData.bio ? "「 " + userData.bio + " 」" : "（一言コメントなし）";

                var modalTags = document.getElementById("modalTags");
                modalTags.innerHTML = "";
                if (userData.tags && userData.tags.length > 0) {
                    userData.tags.forEach(function(tag) {
                        var span = document.createElement("span");
                        span.className = "tag";
                        span.textContent = "#" + tag;
                        modalTags.appendChild(span);
                    });
                } else {
                    modalTags.textContent = "趣味タグなし";
                }

                document.getElementById("profileModal").classList.remove("hidden");
            }

            function closeProfileModal() {
                document.getElementById("profileModal").classList.add("hidden");
            }

            function backToProfile() {
                showScreen("profileScreen");
            }

            function showScreen(screenId) {
                document.getElementById("profileScreen").classList.add("hidden");
                document.getElementById("lobbyScreen").classList.add("hidden");
                document.getElementById("chatScreen").classList.add("hidden");
                document.getElementById(screenId).classList.remove("hidden");
            }
        </script>
    </body>
</html>
"""

@app.get("/")
async def get():
    return HTMLResponse(content=html_content)

@app.websocket("/ws/{room_name}")
async def websocket_endpoint(websocket: WebSocket, room_name: str):
    await manager.connect(room_name, websocket)
    update_room_activity(room_name)
    
    if room_name in room_history:
        for past_data in room_history[room_name]:
            await websocket.send_text(json.dumps(past_data))

    system_msg = {"type": "system", "message": "新しいユーザーが参加しました"}
    await manager.broadcast(room_name, system_msg, save_history=False)
    
    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            
            update_room_activity(room_name)
            await manager.broadcast(room_name, data)
    except WebSocketDisconnect:
        manager.disconnect(room_name, websocket)
        exit_msg = {"type": "system", "message": "ユーザーが退室しました"}
        await manager.broadcast(room_name, exit_msg, save_history=False)
