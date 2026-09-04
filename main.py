import json
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

rooms_db: dict[str, dict] = {}
room_history: dict[str, list[dict]] = {}

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
            for connection in self.rooms[room_name]:
                await connection.send_text(json_data)

manager = ConnectionManager()

class RoomCreate(BaseModel):
    name: str
    tags: list[str] = []
    icon: str = ""

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

html_content = """
<!DOCTYPE html>
<html>
    <head>
        <title>趣味マッチングチャット</title>
        <style>
            body { font-family: sans-serif; margin: 0; padding: 0; background-color: #f9f9f9; }
            .navbar { background: #333; color: white; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; }
            .home-btn { background: #4CAF50; color: white; border: none; padding: 8px 15px; border-radius: 4px; cursor: pointer; font-weight: bold; }
            .screen { margin: 20px; }
            .hidden { display: none !important; }
            .room-card { margin: 8px 0; padding: 10px; border: 1px solid #ccc; border-radius: 8px; width: 340px; background: white; display: flex; align-items: center; gap: 12px; }
            .tag { display: inline-block; background: #e0e0e0; font-size: 11px; padding: 2px 6px; margin-right: 4px; border-radius: 3px; }
            .recommend-tag { background: #ffe082; font-weight: bold; }
            .create-box { margin-top: 15px; padding: 15px; border: 1px solid #aaa; width: 340px; background: white; border-radius: 8px; }
            
            /* アイコン・画像用スタイル */
            .icon-avatar { width: 40px; height: 40px; border-radius: 50%; object-fit: cover; background: #ddd; cursor: pointer; }
            .room-icon-img { width: 48px; height: 48px; border-radius: 8px; object-fit: cover; background: #eee; }
            .preview-img { width: 60px; height: 60px; border-radius: 50%; object-fit: cover; display: block; margin-top: 5px; }

            /* チャットUI */
            #messages { list-style: none; padding: 0; }
            .chat-item { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 12px; }
            .chat-content { display: flex; flex-direction: column; }
            .chat-name { font-size: 12px; color: #666; margin-bottom: 2px; cursor: pointer; font-weight: bold; }
            .chat-text { background: white; padding: 8px 12px; border-radius: 8px; border: 1px solid #ddd; max-width: 350px; word-break: break-all; }
            .chat-send-img { max-width: 250px; max-height: 250px; border-radius: 8px; border: 1px solid #ddd; margin-top: 4px; }
            .system-msg { color: #888; font-style: italic; font-size: 13px; margin: 8px 0; }
            
            /* プロフィールモーダル */
            .modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: flex; justify-content: center; align-items: center; z-index: 1000; }
            .modal-content { background: white; padding: 20px; border-radius: 10px; width: 280px; text-align: center; position: relative; }
            .modal-avatar { width: 70px; height: 70px; border-radius: 50%; object-fit: cover; margin-bottom: 10px; }
            .modal-bio { background: #f0f0f0; padding: 8px; border-radius: 6px; font-size: 13px; margin: 10px 0; color: #444; }

            button { cursor: pointer; }
        </style>
    </head>
    <body>

        <div class="navbar">
            <span>学校趣味チャット</span>
            <button class="home-btn" onclick="goHome()">🏠 ホーム</button>
        </div>

        <!-- 1. プロフィール設定画面 -->
        <div id="profileScreen" class="screen">
            <h1>プロフィール登録</h1>
            <p>名前: <input type="text" id="usernameInput" placeholder="例: たろう" /></p>
            
            <p>一言コメント（プロフィールに表示）:</p>
            <input type="text" id="userBioInput" placeholder="例: よろしくお願いします！" style="width: 280px;" />

            <p>アイコン画像を選択:</p>
            <input type="file" id="userIconInput" accept="image/*" onchange="previewUserIcon(event)" />
            <img id="userIconPreview" class="preview-img hidden" />

            <p>定番ジャンル（選択）:</p>
            <label><input type="checkbox" class="hobby-check" value="アニメ" /> アニメ</label>
            <label><input type="checkbox" class="hobby-check" value="ボカロ" /> ボカロ</label>
            <label><input type="checkbox" class="hobby-check" value="軽音" /> 軽音</label>
            <label><input type="checkbox" class="hobby-check" value="ゲーム" /> ゲーム</label>
            <label><input type="checkbox" class="hobby-check" value="声優" /> 声優</label>
            
            <p>自由に追加する趣味・タグ（カンマ区切り）:</p>
            <input type="text" id="customHobbyInput" placeholder="例: イラスト, 競プロ" style="width: 280px;" />
            <br><br>
            <button onclick="saveProfile()">保存してロビーへ</button>
        </div>

        <!-- 2. ロビー画面 -->
        <div id="lobbyScreen" class="screen hidden">
            <h1 id="welcomeText">ようこそ！</h1>
            
            <div style="color: #d97706;">
                <h3>★ あなたにおすすめのルーム:</h3>
                <div id="recommendedRoomList">該当するルームがありません</div>
            </div>

            <hr>

            <h3>すべてのトークルーム一覧:</h3>
            <div id="allRoomList">まだ部屋がありません。</div>

            <div class="create-box">
                <h4>新しい趣味ルームを作る</h4>
                <p>ルーム名: <input type="text" id="newRoomNameInput" placeholder="例: 競プロ部屋" /></p>
                <p>ルームアイコン: <input type="file" id="newRoomIconInput" accept="image/*" /></p>
                <p>タグ (カンマ区切り): <input type="text" id="newRoomTagsInput" placeholder="例: 競プロ, Python" /></p>
                <button onclick="createNewRoom()">ルームを作成</button>
            </div>

            <br>
            <button onclick="backToProfile()">プロフィール変更</button>
        </div>

        <!-- 3. チャット画面 -->
        <div id="chatScreen" class="screen hidden">
            <h1 id="currentRoomTitle">チャットルーム</h1>
            
            <div style="margin-bottom: 10px; display: flex; gap: 5px;">
                <input type="text" id="messageText" placeholder="メッセージを入力..." style="flex: 1;" />
                <button onclick="sendMessage()">送信</button>
                <button onclick="document.getElementById('chatImageInput').click()">📷 画像</button>
                <input type="file" id="chatImageInput" accept="image/*" class="hidden" onchange="sendImageMessage(event)" />
            </div>
            
            <button onclick="goHome()">退室してホームへ戻る</button>

            <ul id="messages"></ul>
        </div>

        <!-- 4. 相手プロフィール確認用モーダル -->
        <div id="profileModal" class="modal-overlay hidden">
            <div class="modal-content">
                <img id="modalAvatar" class="modal-avatar" src="" />
                <h3 id="modalName" style="margin: 5px 0;"></h3>
                <div id="modalBio" class="modal-bio"></div>
                <div id="modalTags" style="margin-bottom: 15px;"></div>
                <button onclick="closeProfileModal()">閉じる</button>
            </div>
        </div>

        <script>
            var currentUser = "";
            var userBio = "";
            var userIconBase64 = "";
            var userHobbies = [];
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
                    var customTags = [];

                    userHobbies.forEach(function(hobby) {
                        var found = false;
                        checkboxes.forEach(function(cb) {
                            if (cb.value === hobby) {
                                cb.checked = true;
                                found = true;
                            }
                        });
                        if (!found) customTags.push(hobby);
                    });

                    if (customTags.length > 0) {
                        document.getElementById("customHobbyInput").value = customTags.join(", ");
                    }
                }
            };

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

                var customInput = document.getElementById("customHobbyInput").value.trim();
                if (customInput) {
                    var customTags = customInput.split(",").map(t => t.trim()).filter(t => t.length > 0);
                    customTags.forEach(function(tag) {
                        if (!userHobbies.includes(tag)) userHobbies.push(tag);
                    });
                }

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
                    recommendedList.textContent = "あなたのお気に入りジャンルに合う部屋がまだありません。";
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
                enterBtn.textContent = "入室";
                enterBtn.onclick = function() { enterRoom(room.name); };
                div.appendChild(enterBtn);

                return div;
            }

            async function createNewRoom() {
                var nameInput = document.getElementById("newRoomNameInput");
                var tagsInput = document.getElementById("newRoomTagsInput");
                var iconInput = document.getElementById("newRoomIconInput");

                var roomName = nameInput.value.trim();
                var tagsRaw = tagsInput.value.trim();

                if (!roomName) {
                    alert("ルーム名を入力してください");
                    return;
                }

                var roomIconBase64 = "";
                if (iconInput.files && iconInput.files[0]) {
                    roomIconBase64 = await fileToBase64(iconInput.files[0]);
                }

                var tags = tagsRaw ? tagsRaw.split(",").map(t => t.trim()) : [];

                await fetch("/api/rooms", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: roomName, tags: tags, icon: roomIconBase64 })
                });

                nameInput.value = "";
                tagsInput.value = "";
                iconInput.value = "";
                loadRooms();
            }

            function enterRoom(roomName) {
                document.getElementById("currentRoomTitle").textContent = "「" + roomName + "」の部屋";
                document.getElementById("messages").innerHTML = "";

                ws = new WebSocket("ws://" + window.location.host + "/ws/" + encodeURIComponent(roomName));

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
                    sysDiv.textContent = data.message;
                    messages.appendChild(sysDiv);
                } else {
                    var li = document.createElement("li");
                    li.className = "chat-item";

                    // アイコンと名前にクリックイベントを付与（プロフィールモーダル表示）
                    var img = document.createElement("img");
                    img.className = "icon-avatar";
                    img.src = data.icon || "https://via.placeholder.com/40?text=User";
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
            }

            // テキストメッセージの送信
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

            // 画像メッセージの送信
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
                    event.target.value = ""; // 入力をリセット
                }
            }

            // 相手のプロフィール確認用モーダル表示
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