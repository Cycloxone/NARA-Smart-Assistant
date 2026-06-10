import asyncio
import websockets
import json
import threading
import torch
import speech_recognition as sr
import os
from gtts import gTTS
import pygame
from transformers import AutoTokenizer, AutoModelForCausalLM
from ultralytics import YOLO
import cv2
import paho.mqtt.client as mqtt
import time
import numpy as np
import librosa
import joblib
import requests
import smtplib
from email.mime.text import MIMEText

# ============================================================
# KONFIGURASI
# ============================================================
WS_HOST = "localhost"
WS_PORT = 8765
BROKER   = "10.252.251.87"
MQTT_PORT = 1883

# ============================================================
# STATE GLOBAL
# ============================================================
connected_clients = set() 
current_speaker   = "unknown"

# ============================================================
# INISIALISASI MODEL
# ============================================================
print("Sedang memuat model Qwen/Qwen2.5-1.5B-Instruct...")
model_name = "Qwen/Qwen2.5-1.5B-Instruct"
tokenizer  = AutoTokenizer.from_pretrained(model_name)
model      = AutoModelForCausalLM.from_pretrained(
    model_name, device_map="auto", torch_dtype=torch.float16
)
pygame.mixer.init()
print("Model Qwen siap!")

print("Memuat model YOLO (food)...")
yolo_model = YOLO("best.pt")

print("Memuat model YOLO (person)...")
yolo_person_model = YOLO("best_result.pt")

print("Memuat model Speaker Recognition (SVM)...")
try:
    speaker_model = joblib.load("model_sidik_suara.pkl")
    print("Model SVM siap!")
except Exception as e:
    print(f"Gagal memuat model suara: {e}")
    speaker_model = None

# ============================================================
# KALORI DATABASE 
# ============================================================

def load_kalori_from_db() -> dict:
    """Ambil seluruh data kalori dari tabel food_calories via nara_api.php.
    Return dict {food_name: calories}, mis: {'Ayam Goreng': 260}
    Fallback ke dict kosong jika DB tidak bisa diakses."""
    try:
        resp = requests.post(
            DB_API_URL,
            json={"action": "get_calories_dict"},
            timeout=5
        )
        data = resp.json()
        if data.get("status") == "ok" and data.get("data"):
            result = {k: int(v) for k, v in data["data"].items()}
            print(f"  {len(result)} item kalori berhasil dimuat dari DB")
            return result
    except Exception as e:
        print(f"[WARN] Gagal memuat kalori dari DB: {e}")
    return {}

def get_calorie_by_name(food_name: str) -> int:
    """Cari kalori 1 makanan by nama, langsung dari DB (selalu fresh).
    Dipakai saat deteksi makanan agar data real-time (tidak bergantung cache)."""
    try:
        resp = requests.post(
            DB_API_URL,
            json={"action": "get_calorie_by_name", "food_name": food_name},
            timeout=5
        )
        data = resp.json()
        if data.get("status") == "ok" and data.get("data"):
            return int(data["data"]["calories"])
    except Exception as e:
        print(f"[WARN] get_calorie_by_name gagal: {e}")
    # Fallback ke cache lokal jika DB tidak merespons
    return _kalori_cache.get(food_name, 0)

# ============================================================
# KONTAK 
# ============================================================
DB_API_URL = "http://nara.test:8080/nara_api.php"

def load_contacts_from_db() -> dict:
    """Ambil semua kontak dari MySQL via nara_api.php.
    Return dict {nickname: email}, mis: {'owen': 'owen@email.com'}
    Fallback ke dict kosong jika DB tidak bisa diakses."""
    try:
        resp = requests.post(
            DB_API_URL,
            json={"action": "get_contacts"},
            timeout=5
        )
        data = resp.json()
        if data.get("status") == "ok" and data.get("data"):
            return {c["nickname"]: c["email"] for c in data["data"]}
    except Exception as e:
        print(f"[WARN] Gagal memuat kontak dari DB: {e}")
    return {}

def get_contact_email(nickname: str) -> str | None:
    """Cari email kontak by nickname, langsung ke DB (selalu fresh)."""
    try:
        resp = requests.post(
            DB_API_URL,
            json={"action": "get_contact_by_nickname", "nickname": nickname.lower()},
            timeout=5
        )
        data = resp.json()
        if data.get("status") == "ok" and data.get("data"):
            return data["data"]["email"]
    except Exception as e:
        print(f"[WARN] get_contact_email gagal: {e}")
    return None


print("Memuat kontak dari database...")
CONTACTS = load_contacts_from_db()
if CONTACTS:
    print(f"  {len(CONTACTS)} kontak berhasil dimuat dari DB")
else:
    print("  [WARN] Kontak kosong — pastikan DB terhubung dan nara_api.php berjalan")

# Load kalori saat startup (cache lokal sebagai fallback)
print("Memuat data kalori dari database...")
_kalori_cache = load_kalori_from_db()
if not _kalori_cache:
    print("  [WARN] Data kalori kosong — pastikan tabel food_calories sudah diisi")


tools = [
    {"name": "get_weather", "description": "Get the current weather for a specific location",
     "parameters": {"type": "object", "properties": {"location": {"type": "string"}, "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}}, "required": ["location"]}},
    {"name": "answer_general_question", "description": "Use this function to answer general knowledge questions.",
     "parameters": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}},
    {"name": "send_email", "description": "Send an email to a specific recipient.",
     "parameters": {"type": "object", "properties": {"recipient": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["recipient", "subject", "body"]}},
    {"name": "get_latest_news", "description": "Ambil berita terbaru berdasarkan topik tertentu.",
     "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}},
    {"name": "estimate_calories", "description": "Buka kamera untuk mendeteksi makanan dan menghitung kalori.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "kontrol_smarthome", "description": "Kontrol lampu dan tirai via MQTT.",
     "parameters": {"type": "object", "properties": {"perangkat": {"type": "string", "enum": ["lampu", "tirai"]}, "perintah": {"type": "string", "enum": ["nyala", "mati", "buka", "tutup"]}}, "required": ["perangkat", "perintah"]}},
    {"name": "cek_orang_dan_nyalakan_lampu", "description": "Cek kamera apakah ada orang di ruangan.",
     "parameters": {"type": "object", "properties": {"aksi": {"type": "string"}}, "required": ["aksi"]}},
]

# ============================================================
# FUNGSI UTILITAS WEBSOCKET
# ============================================================

# Loop asyncio utama disimpan di sini agar bisa diakses dari thread manapun
_main_loop: asyncio.AbstractEventLoop | None = None

async def broadcast(message: dict):
    """Kirim pesan ke SEMUA klien GUI yang terhubung."""
    if not connected_clients:
        return
    data = json.dumps(message, ensure_ascii=False)
    clients = list(connected_clients)
    for client in clients:
        try:
            await client.send(data)
        except Exception:
            connected_clients.discard(client)

def broadcast_sync(message: dict):
    """Versi sinkronus dari broadcast (aman dipanggil dari thread manapun)."""
    global _main_loop
    if _main_loop is None or not _main_loop.is_running():
        # Loop belum siap, abaikan saja (biasanya saat startup)
        return
    try:
        asyncio.run_coroutine_threadsafe(broadcast(message), _main_loop)
    except Exception as e:
        print(f"[WS] Gagal broadcast: {e}")

def gui_log(level: str, msg: str):
    """Kirim entri log ke GUI."""
    print(f"[{level}] {msg}")
    broadcast_sync({"type": "log", "level": level, "msg": msg})

def gui_status(mode: str, label: str):
    """Update status bar di GUI."""
    broadcast_sync({"type": "status", "status": mode, "label": label})

def gui_speaker(who: str, confidence: float):
    """Update panel speaker recognition di GUI."""
    broadcast_sync({"type": "speaker", "who": who, "confidence": confidence})

def gui_iot(device: str, state: str):
    """Update tombol IoT di GUI."""
    broadcast_sync({"type": "iot", "device": device, "state": state})

def gui_response(text: str, tool: str = None):
    """Kirim respons Nara ke chat GUI."""
    broadcast_sync({"type": "response", "text": text, "tool": tool})

def gui_user_text(text: str, speaker: str = "unknown"):
    """Tampilkan teks pengguna di chat GUI, beserta info speaker agar disimpan dengan benar."""
    broadcast_sync({"type": "user_text", "text": text, "speaker": speaker})

# ============================================================
# FUNGSI TTS
# ============================================================

def speak(text: str, tool: str = None):
    if not text:
        return
    print(f"NARA: {text}")
    gui_response(text, tool)
    try:
        tts = gTTS(text=text, lang="id")
        fname = "temp_nara.mp3"
        tts.save(fname)
        pygame.mixer.music.load(fname)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        pygame.mixer.music.unload()
        os.remove(fname)
    except Exception as e:
        gui_log("ERR", f"TTS gagal: {e}")

# ============================================================
# FUNGSI EKSEKUSI
# ============================================================

def execute_weather_function(location, unit="celsius"):
    gui_log("TOOL", f"get_weather → {location}")
    try:
        geo = requests.get(f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1&language=id&format=json", timeout=10).json()
        if not geo.get("results"):
            return f"Lokasi '{location}' tidak ditemukan."
        r = geo["results"][0]
        lat, lon, city = r["latitude"], r["longitude"], r.get("name", location)
        weather = requests.get("https://api.open-meteo.com/v1/forecast", params={"latitude": lat, "longitude": lon, "current_weather": "true", "timezone": "auto"}, timeout=10).json()
        cur = weather.get("current_weather")
        if cur:
            return f"Cuaca di {city} saat ini adalah {cur['temperature']} derajat Celcius."
        return "Gagal mengambil data cuaca."
    except Exception as e:
        return f"Masalah koneksi: {e}"

def execute_send_email(recipient_input, subject, body):
    gui_log("TOOL", f"send_email → {recipient_input}")
    recipient_input = recipient_input.lower().strip()

    email_addr = get_contact_email(recipient_input)
    if not email_addr:
        email_addr = CONTACTS.get(recipient_input)
    if not email_addr:
        email_addr = recipient_input if "@" in recipient_input else None
    if not email_addr:
        gui_log("ERR", f"Kontak '{recipient_input}' tidak ditemukan di database")
        return f"Kontak '{recipient_input}' tidak ditemukan. Pastikan kontak sudah ditambahkan di GUI."

    sender = "smartassistant.calvin@gmail.com"
    password = "jdyc ljtj yhkr hmgr"
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = email_addr
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(sender, password)
            s.sendmail(sender, email_addr, msg.as_string())
        gui_log("INFO", f"Email terkirim ke {email_addr}")
        return f"Email berhasil dikirim ke {recipient_input} ({email_addr})."
    except Exception as e:
        return f"Gagal mengirim email: {e}"

def execute_news_function(topic):
    gui_log("TOOL", f"get_latest_news → {topic}")
    try:
        API_KEY = "ecbd9352be57617496b9b8891f000719"
        url = f"https://gnews.io/api/v4/search?q={topic}&lang=id&country=id&max=3&apikey={API_KEY}"
        data = requests.get(url, timeout=10).json()
        articles = data.get("articles", [])
        if not articles:
            return f"Tidak ada berita terbaru tentang {topic}."
        hasil = f"Berikut berita terbaru tentang {topic}: "
        for i, a in enumerate(articles, 1):
            hasil += f"\n{i}. {a['title']}"
        return hasil
    except Exception as e:
        return f"Gagal mengambil berita: {e}"

def execute_calorie_estimation():
    gui_log("TOOL", "estimate_calories → membuka kamera")
    speak("Kamera terbuka. Silakan arahkan makanan, lalu tekan spasi jika sudah pas.")
    cap = cv2.VideoCapture(0)
    makanan_final = None
    kalori_final = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        results = yolo_model(frame, verbose=False)
        conf = results[0].probs.top1conf.item()
        makanan_saat_ini = "Mencari..."
        if conf > 0.98:
            idx = results[0].probs.top1
            makanan_saat_ini = results[0].names[idx]
            kalori_saat_ini = get_calorie_by_name(makanan_saat_ini)  # query DB langsung
            text = f"{makanan_saat_ini} | {kalori_saat_ini} Kkal"
            cv2.rectangle(frame, (10, 20), (500, 70), (0, 0, 0), -1)
            cv2.putText(frame, text, (20, 55), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        else:
            cv2.putText(frame, "Menganalisis...", (20, 55), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 200, 255), 2)
        cv2.imshow("Mata Nara (SPASI=Konfirmasi, Q=Batal)", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord(" ") and makanan_saat_ini != "Mencari...":
            makanan_final = makanan_saat_ini
            kalori_final = kalori_saat_ini
            break
        elif key == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
    if makanan_final:
        return f"Saya melihat {makanan_final}. Estimasi kalorinya adalah {kalori_final} kilo kalori."
    return "Kamera dibatalkan atau tidak ada makanan terdeteksi."

def kontrol_smarthome(perangkat, perintah):
    gui_log("TOOL", f"kontrol_smarthome → {perangkat}:{perintah}")
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(BROKER, MQTT_PORT, 60)
        client.publish(f"nara/kamar/{perangkat}", perintah)
        client.disconnect()
        # Update GUI
        state = perintah  # 'nyala'/'mati'/'buka'/'tutup'
        gui_iot(perangkat, state)
        return f"Sistem berhasil menyuruh {perangkat} untuk {perintah}."
    except Exception as e:
        return f"Gagal terhubung ke IoT: {e}"

def execute_person_detection():
    gui_log("TOOL", "cek_orang → membuka kamera")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return "Tidak bisa mengakses kamera."
    for _ in range(10):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return "Gagal mengambil gambar dari kamera."
    results = yolo_person_model.predict(source=frame, conf=0.6, verbose=False)
    person_detected = len(results[0].boxes) > 0
    if person_detected:
        kontrol_smarthome("lampu", "nyala")
        return "Saya melihat ada orang di ruangan. Lampu kamar sudah saya nyalakan."
    return "Ruangan terlihat kosong. Lampu tidak dinyalakan."

# ============================================================
# PROSES INSTRUKSI
# ============================================================

def process_instruction(user_input: str) -> str:
    messages = [
        {"role": "system", "content": (
            "Kamu adalah Nara, asisten AI cerdas untuk mengontrol rumah. "
            "ATURAN: 1. Lampu/tirai → 'kontrol_smarthome'. "
            "2. Cek orang/kamera → 'cek_orang_dan_nyalakan_lampu'. "
            "3. Kalori/makanan → 'estimate_calories'. "
            "4. Cuaca → 'get_weather'. "
            "5. Email → 'send_email'. "
            "6. Berita → 'get_latest_news'. "
            "7. Pertanyaan umum → 'answer_general_question'. "
            "Kontak tersedia: owen, steven, theodore, sari, niki, kanato, brian, renata, jemima, petra, gery, albert."
        )},
        {"role": "user", "content": user_input},
    ]
    inputs = tokenizer.apply_chat_template(
        messages, tools=tools, add_generation_prompt=True,
        return_dict=True, return_tensors="pt"
    )
    inputs = {k: v.to(model.device) for k, v in inputs.items()}
    outputs = model.generate(**inputs, max_new_tokens=256)
    return tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)

def handle_llm_response(llm_response: str, user_input: str):
    """Parse respons LLM dan jalankan fungsi yang tepat, lalu kirim hasilnya."""
    gui_log("LLM", f"raw: {llm_response[:80]}...")
    try:
        clean = llm_response.strip().replace("```json", "").replace("```", "")
        if "{" in clean and "}" in clean:
            start = clean.find("{"); end = clean.rfind("}") + 1
            data = json.loads(clean[start:end])
            if isinstance(data, list):
                data = data[0]
            func_name = data.get("name", "")
            args = data.get("arguments") or data.get("parameters") or data

            # ---- ROUTING ----
            # ---- ROUTING ----
            if "get_weather" in func_name or "location" in str(args):
                lokasi = args.get("location") or args.get("city", "Jakarta")
                hasil = execute_weather_function(lokasi)
                speak(hasil, "get_weather")
                # gui_response(hasil, "get_weather")  <-- BARIS INI DIHAPUS

            elif "email" in func_name or "recipient" in str(args):
                target = args.get("recipient", "").lower().strip()
                sub    = args.get("subject", "Tanpa Subjek")
                isi    = args.get("body", "")
                if CONTACTS.get(target) or "@" in target:
                    hasil = execute_send_email(target, sub, isi)
                else:
                    hasil = f"Kontak '{target}' tidak ditemukan."
                speak(hasil, "send_email")

            elif "news" in func_name or "topic" in str(args):
                topic = args.get("topic", user_input)
                hasil = execute_news_function(topic)
                speak(hasil, "get_latest_news")

            elif "calories" in func_name or "estimate" in func_name:
                hasil = execute_calorie_estimation()
                speak(hasil, "estimate_calories")

            elif "smarthome" in func_name:
                perangkat = args.get("perangkat")
                perintah  = args.get("perintah")
                if perangkat and perintah:
                    hasil = kontrol_smarthome(perangkat, perintah)
                    speak(f"Siap! {perangkat} sudah {perintah}.", "kontrol_smarthome")
                else:
                    speak("Perintah smart home kurang jelas.")

            elif "cek_orang" in func_name:
                speak("Sebentar, saya lihat dulu ya.")
                hasil = execute_person_detection()
                speak(hasil, "cek_orang_dan_nyalakan_lampu")

            elif "answer" in str(args):
                jawaban = args.get("answer", "")
                speak(jawaban, "answer_general_question")

            else:
                speak(clean)

        else:
            speak(clean, "answer_general_question")

    except Exception as e:
        gui_log("ERR", f"parse error: {e}")
        speak(llm_response)

# ============================================================
# SPEAKER RECOGNITION
# ============================================================

def identify_speaker(audio_data) -> tuple[str, float]:
    """Kembalikan (nama, confidence). Nama: 'owen'|'steven'|'unknown'."""
    if speaker_model is None:
        return "unknown", 0.0
    try:
        temp = "temp_speaker.wav"
        with open(temp, "wb") as f:
            f.write(audio_data.get_wav_data())
        audio_arr, sr_val = librosa.load(temp, sr=16000)
        audio_arr, _ = librosa.effects.trim(audio_arr, top_db=20)
        mfccs = librosa.feature.mfcc(y=audio_arr, sr=sr_val, n_mfcc=40)
        fitur = np.mean(mfccs.T, axis=0)
        pred  = speaker_model.predict([fitur])[0]
        proba = speaker_model.predict_proba([fitur])[0]
        conf  = float(np.max(proba))
        os.remove(temp)
        if conf < 0.65:
            return "unknown", conf
        mapping = {0: "owen", 1: "steven"}
        return mapping.get(pred, "unknown"), conf
    except Exception as e:
        gui_log("ERR", f"identify_speaker: {e}")
        return "unknown", 0.0

# ============================================================
# LOOP MIKROFON
# ============================================================

def microphone_loop():
    """Loop utama mendengarkan wakeword, sama persis dengan kode asli."""
    global current_speaker
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        gui_log("MIC", "kalibrasi ambient noise...")
        recognizer.adjust_for_ambient_noise(source, duration=2)
        gui_log("MIC", "siap mendengarkan!")
        gui_status("active", "standby")

        while True:
            # 1. Tunggu wakeword
            try:
                audio = recognizer.listen(source, timeout=1, phrase_time_limit=2)
                text  = recognizer.recognize_google(audio, language="id-ID").lower()
                is_wake = any(w in text for w in ["halo nara", "halo nera", "halo nerew"])
            except:
                is_wake = False
                audio   = None

            if not (is_wake and audio):
                continue

            # 2. Wakeword terdeteksi — notifikasi GUI
            broadcast_sync({"type": "wakeword", "detected": True})
            broadcast_sync({"type": "waveform", "active": True})
            gui_status("listening", "mendengarkan...")

            # 3. Identifikasi pembicara
            who, conf = identify_speaker(audio)
            current_speaker = who
            gui_speaker(who, conf)

            sapa = {
                "owen":   "Iya, Owen, ada yang bisa Nara bantu?",
                "steven": "Iya, Steven, ada yang bisa Nara bantu?",
            }.get(who, "Iya, ada yang bisa Nara bantu?")
            speak(sapa)

            # 4. Dengarkan perintah
            gui_status("listening", "menunggu perintah...")
            try:
                cmd_audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
                user_text = recognizer.recognize_google(cmd_audio, language="id-ID")
                print(f"USER ({who}): {user_text}")
            except:
                speak("Maaf, saya tidak mendengar perintahmu.")
                gui_status("active", "standby")
                broadcast_sync({"type": "waveform", "active": False})
                continue

            if "keluar" in user_text.lower():
                speak("Sampai jumpa lagi!")
                gui_status("active", "offline")
                break

            # 5. Tampilkan teks user di GUI — sertakan speaker agar tidak ada race condition
            gui_user_text(user_text, speaker=who)

            # 6. Proses dengan LLM
            gui_status("thinking", "berpikir...")
            t0 = time.time()
            llm_resp = process_instruction(user_text)
            gui_log("TIME", f"selesai dalam {time.time()-t0:.2f} detik")

            broadcast_sync({"type": "waveform", "active": False})
            handle_llm_response(llm_resp, user_text)
            gui_status("active", "standby")

# ============================================================
# WEBSOCKET SERVER — Terima perintah dari GUI
# ============================================================

async def ws_handler(websocket):
    """Handler untuk setiap koneksi GUI baru."""
    connected_clients.add(websocket)
    addr = websocket.remote_address
    gui_log("WS", f"GUI terhubung dari {addr}")
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                if msg_type == "command":
                    text = msg.get("text", "").strip()
                    if text:
                        gui_log("GUI", f'perintah teks: "{text}"')
                        # Jalankan di thread terpisah agar tidak blokir event loop
                        await _main_loop.run_in_executor(None, _run_command, text)

                elif msg_type == "start_listening":
                    gui_log("MIC", "GUI minta mulai dengarkan")
                    # Mikrofon sudah berjalan di thread sendiri; GUI hanya diminta notifikasi

                elif msg_type == "stop_listening":
                    gui_log("MIC", "GUI minta berhenti dengarkan")

            except json.JSONDecodeError:
                gui_log("ERR", "pesan WebSocket bukan JSON yang valid")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_clients.discard(websocket)
        gui_log("WS", f"GUI {addr} terputus")

def _run_command(text: str):
    """Jalankan perintah teks (dari GUI, bukan suara) secara sinkronus."""
    gui_status("thinking", "berpikir...")
    t0 = time.time()
    resp = process_instruction(text)
    gui_log("TIME", f"selesai dalam {time.time()-t0:.2f} detik")
    handle_llm_response(resp, text)
    gui_status("active", "standby")

# ============================================================
# MAIN — Jalankan WebSocket server + mic loop secara paralel
# ============================================================

async def main():
    global _main_loop
    print(f"\n{'='*50}")
    print(f"  NARA Backend WebSocket Server")
    print(f"  Listening pada ws://{WS_HOST}:{WS_PORT}")
    print(f"  Buka nara_gui.html di browser Anda")
    print(f"{'='*50}\n")

    # Simpan referensi loop utama SEBELUM thread mikrofon dimulai
    _main_loop = asyncio.get_running_loop()

    # Jalankan loop mikrofon di background thread
    mic_thread = threading.Thread(target=microphone_loop, daemon=True)
    mic_thread.start()

    # Jalankan WebSocket server di event loop utama
    async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
        await asyncio.Future()  # Jalan selamanya

if __name__ == "__main__":
    asyncio.run(main())