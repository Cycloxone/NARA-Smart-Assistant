# NARA-Smart-Assistant
A smart voice assistant equipped with robust Speech-to-Text and Text-to-Speech integration for seamless hands-free interaction.

## ✨ Key Features

* **🎙️ Voice-Activated & Speaker Recognition:** Constantly listens for the wakeword (`"Halo Nara"`). Uses an SVM model with MFCC feature extraction to recognize the registered speaker's voice (e.g., Owen or Steven) before executing commands.
* **🧠 LLM-Powered Function Calling:** Utilizes a locally-loaded LLM (like `xLAM` or `Qwen`) to naturally route user intents. It distinguishes between general conversations and specific tool executions without strict hardcoded if-else logic.
* **👁️ Computer Vision Integration:**
    * **Calorie Estimator:** Uses a YOLO model (`best.pt`) via OpenCV to detect food items through the camera and queries a MySQL database for real-time calorie estimation.
    * **Presence Detection:** Uses a YOLO person detection model (`best_result.pt`) to scan rooms and trigger smart home actions automatically.
* **💡 Smart Home IoT Control:** Integrates with an MQTT Broker to control physical smart devices such as lamps and window curtains in real-time.
* **🌐 External APIs Integration:** Capable of sending emails via SMTP, fetching real-time weather forecasts (Open-Meteo), and reading the latest news (GNews API).
* **🖥️ Real-Time WebSocket GUI:** Features a responsive web-based graphical interface (`nara_gui.html`) that communicates with the Python backend via WebSockets to display system status, recognized speech, camera feeds, and LLM responses in real-time.

---

## 🛠️ Tech Stack & Architecture

* **Core AI / LLM:** Hugging Face `transformers`, PyTorch, xLAM / Qwen
* **Speech-to-Text (STT):** Google Web Speech API (`SpeechRecognition` library)
* **Text-to-Speech (TTS):** Google TTS (`gTTS`) & `pygame` for audio playback
* **Computer Vision:** OpenCV, Ultralytics YOLOv8/v11
* **Audio Processing:** `librosa`, `scikit-learn` (SVM)
* **IoT Protocol:** `paho-mqtt`
* **Backend Server:** `asyncio`, `websockets`
* **Database Integration:** MySQL (via PHP backend API)
