<?php

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, GET, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { http_response_code(200); exit; }

// ── KONFIGURASI DATABASE ────────────────────────────────────
define('DB_HOST', 'localhost');
define('DB_USER', 'root');       
define('DB_PASS', '');           
define('DB_NAME', 'nara_db');

// ── Koneksi PDO ─────────────────────────────────────────────
function getDB(): PDO {
    static $pdo = null;
    if ($pdo === null) {
        $dsn = 'mysql:host=' . DB_HOST . ';dbname=' . DB_NAME . ';charset=utf8mb4';
        $pdo = new PDO($dsn, DB_USER, DB_PASS, [
            PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
    }
    return $pdo;
}

function ok($data = null, string $msg = 'ok'): void {
    echo json_encode(['status' => 'ok', 'msg' => $msg, 'data' => $data]);
    exit;
}

function err(string $msg, int $code = 400): void {
    http_response_code($code);
    echo json_encode(['status' => 'error', 'msg' => $msg, 'data' => null]);
    exit;
}

// ── Baca body JSON ──────────────────────────────────────────
$body   = json_decode(file_get_contents('php://input'), true) ?? [];
$action = $body['action'] ?? ($_GET['action'] ?? '');

try {
    $db = getDB();

    switch ($action) {

        // ── Mulai sesi baru ─────────────────────────────────
        case 'start_session':
            $sid     = $body['session_id'] ?? '';
            $speaker = $body['speaker'] ?? 'unknown';
            if (!$sid) err('session_id wajib diisi');

            $db->prepare("
                INSERT INTO sessions (session_id, speaker, started_at, total_msgs)
                VALUES (:sid, :spk, NOW(), 0)
                ON DUPLICATE KEY UPDATE speaker = :spk2, started_at = NOW()
            ")->execute([':sid' => $sid, ':spk' => $speaker, ':spk2' => $speaker]);

            ok(null, "sesi $sid dimulai");

        // ── Tutup sesi ──────────────────────────────────────
        case 'end_session':
            $sid = $body['session_id'] ?? '';
            if (!$sid) err('session_id wajib diisi');

            $db->prepare("
                UPDATE sessions SET ended_at = NOW() WHERE session_id = :sid
            ")->execute([':sid' => $sid]);

            ok(null, "sesi $sid ditutup");

        // ── Simpan pesan ────────────────────────────────────
        case 'save_msg':
            $sid     = $body['session_id'] ?? '';
            $role    = $body['role']        ?? '';
            $message = $body['message']     ?? '';
            $tool    = $body['tool_used']   ?? null;
            $speaker = $body['speaker']     ?? 'unknown';

            if (!$sid || !$role || !$message) err('session_id, role, message wajib');
            if (!in_array($role, ['user', 'nara'])) err('role harus user atau nara');

            $db->prepare("
                INSERT INTO chat_history (session_id, role, message, tool_used, speaker, created_at)
                VALUES (:sid, :role, :msg, :tool, :spk, NOW())
            ")->execute([':sid' => $sid, ':role' => $role, ':msg' => $message,
                         ':tool' => $tool, ':spk' => $speaker]);

            // Update counter sesi
            $db->prepare("
                UPDATE sessions SET total_msgs = total_msgs + 1 WHERE session_id = :sid
            ")->execute([':sid' => $sid]);

            ok(['id' => $db->lastInsertId()], 'pesan tersimpan');

        // ── Ambil semua sesi (untuk sidebar) ────────────────
        case 'get_sessions':
            $stmt = $db->query("
                SELECT
                    s.session_id,
                    s.speaker,
                    s.started_at,
                    s.ended_at,
                    s.total_msgs,
                    (SELECT message FROM chat_history h
                     WHERE h.session_id = s.session_id AND h.role = 'user'
                     ORDER BY h.created_at ASC LIMIT 1) AS first_msg
                FROM sessions s
                ORDER BY s.started_at DESC
                LIMIT 100
            ");
            ok($stmt->fetchAll());

        // ── Ambil pesan dalam 1 sesi ─────────────────────────
        case 'get_messages':
            $sid = $body['session_id'] ?? '';
            if (!$sid) err('session_id wajib');

            $stmt = $db->prepare("
                SELECT role, message, tool_used, speaker, created_at
                FROM chat_history
                WHERE session_id = :sid
                ORDER BY created_at ASC
            ");
            $stmt->execute([':sid' => $sid]);
            ok($stmt->fetchAll());

        // ── Hapus sesi beserta pesannya ──────────────────────
        case 'delete_session':
            $sid = $body['session_id'] ?? '';
            if (!$sid) err('session_id wajib');

            // CASCADE akan menghapus chat_history otomatis
            $db->prepare("DELETE FROM sessions WHERE session_id = :sid")
               ->execute([':sid' => $sid]);

            ok(null, "sesi $sid dihapus");

        // ── Update speaker setelah SVM mengidentifikasi suara ──
        // Dipanggil SETELAH wakeword sesi sudah berjalan; mengupdate
        // kolom speaker di tabel sessions DAN di semua baris chat_history
        // sesi ini yang masih bernilai 'unknown'.
        case 'update_session_speaker':
            $sid     = $body['session_id'] ?? '';
            $speaker = $body['speaker']    ?? '';
            if (!$sid || !$speaker) err('session_id dan speaker wajib diisi');
            if (!in_array($speaker, ['owen', 'steven', 'unknown'])) err('speaker tidak valid');

            // 1. Update tabel sessions
            $db->prepare("
                UPDATE sessions SET speaker = :spk WHERE session_id = :sid
            ")->execute([':spk' => $speaker, ':sid' => $sid]);

            // 2. Update baris chat_history yang masih 'unknown' di sesi ini
            //    (pesan yang sudah terlanjur masuk sebelum SVM selesai)
            $db->prepare("
                UPDATE chat_history
                SET speaker = :spk
                WHERE session_id = :sid AND (speaker IS NULL OR speaker = 'unknown')
            ")->execute([':spk' => $speaker, ':sid' => $sid]);

            $affected = $db->query("SELECT ROW_COUNT()")->fetchColumn();
            ok(['updated_rows' => (int)$affected],
               "speaker sesi $sid diperbarui ke '$speaker' ($affected pesan diupdate)");


        // ── Cari dalam riwayat ───────────────────────────────
        case 'search':
            $q = '%' . ($body['query'] ?? '') . '%';
            $stmt = $db->prepare("
                SELECT DISTINCT
                    s.session_id, s.speaker, s.started_at, s.total_msgs,
                    (SELECT message FROM chat_history h2
                     WHERE h2.session_id = s.session_id AND h2.role = 'user'
                     ORDER BY h2.created_at ASC LIMIT 1) AS first_msg
                FROM chat_history c
                JOIN sessions s ON s.session_id = c.session_id
                WHERE c.message LIKE :q
                ORDER BY s.started_at DESC
                LIMIT 50
            ");
            $stmt->execute([':q' => $q]);
            ok($stmt->fetchAll());

        // ── Ambil semua kontak ───────────────────────────────
        case 'get_contacts':
            $stmt = $db->query("
                SELECT id, name, nickname, email, phone, avatar_bg, notes
                FROM contacts ORDER BY name ASC
            ");
            ok($stmt->fetchAll());

        // ── Tambah kontak baru ───────────────────────────────
        case 'add_contact':
            $name     = trim($body['name']     ?? '');
            $nickname = strtolower(trim($body['nickname'] ?? ''));
            $email    = trim($body['email']    ?? '');
            $phone    = trim($body['phone']    ?? '');
            $bg       = trim($body['avatar_bg'] ?? '#5DCAA5');
            $notes    = trim($body['notes']    ?? '');
            if (!$name || !$nickname || !$email) err('name, nickname, email wajib diisi');

            // Cek duplikat nickname
            $chk = $db->prepare("SELECT id FROM contacts WHERE nickname = :n");
            $chk->execute([':n' => $nickname]);
            if ($chk->fetch()) err("Nickname '$nickname' sudah dipakai kontak lain");

            $db->prepare("
                INSERT INTO contacts (name, nickname, email, phone, avatar_bg, notes)
                VALUES (:name, :nick, :email, :phone, :bg, :notes)
            ")->execute([
                ':name' => $name, ':nick' => $nickname, ':email' => $email,
                ':phone' => $phone ?: null, ':bg' => $bg, ':notes' => $notes ?: null
            ]);
            ok(['id' => $db->lastInsertId()], "Kontak $name ditambahkan");

        // ── Edit kontak ──────────────────────────────────────
        case 'edit_contact':
            $id       = (int)($body['id']       ?? 0);
            $name     = trim($body['name']      ?? '');
            $nickname = strtolower(trim($body['nickname'] ?? ''));
            $email    = trim($body['email']     ?? '');
            $phone    = trim($body['phone']     ?? '');
            $bg       = trim($body['avatar_bg'] ?? '#5DCAA5');
            $notes    = trim($body['notes']     ?? '');
            if (!$id || !$name || !$nickname || !$email) err('id, name, nickname, email wajib');

            // Cek duplikat nickname (selain diri sendiri)
            $chk = $db->prepare("SELECT id FROM contacts WHERE nickname = :n AND id != :id");
            $chk->execute([':n' => $nickname, ':id' => $id]);
            if ($chk->fetch()) err("Nickname '$nickname' sudah dipakai kontak lain");

            $db->prepare("
                UPDATE contacts SET name=:name, nickname=:nick, email=:email,
                  phone=:phone, avatar_bg=:bg, notes=:notes
                WHERE id=:id
            ")->execute([
                ':name' => $name, ':nick' => $nickname, ':email' => $email,
                ':phone' => $phone ?: null, ':bg' => $bg, ':notes' => $notes ?: null, ':id' => $id
            ]);
            ok(null, "Kontak $name diperbarui");

        // ── Hapus kontak ─────────────────────────────────────
        case 'delete_contact':
            $id = (int)($body['id'] ?? 0);
            if (!$id) err('id wajib');
            $db->prepare("DELETE FROM contacts WHERE id = :id")->execute([':id' => $id]);
            ok(null, 'Kontak dihapus');

        // ── Cari kontak by nickname (untuk backend Python) ───
        case 'get_contact_by_nickname':
            $nick = strtolower(trim($body['nickname'] ?? ''));
            if (!$nick) err('nickname wajib');
            $stmt = $db->prepare("SELECT * FROM contacts WHERE nickname = :n LIMIT 1");
            $stmt->execute([':n' => $nick]);
            $row = $stmt->fetch();
            if (!$row) err("Kontak '$nick' tidak ditemukan", 404);
            ok($row);

        // ════════════════════════════════════════════════════
        // FOOD CALORIES — CRUD
        // ════════════════════════════════════════════════════

        // ── Ambil semua data kalori ──────────────────────────
        case 'get_food_calories':
            $stmt = $db->query("
                SELECT id, food_name, calories, unit, notes
                FROM food_calories
                ORDER BY food_name ASC
            ");
            ok($stmt->fetchAll());

        // ── Ambil kalori 1 makanan by nama (untuk backend Python) ──
        case 'get_calorie_by_name':
            $name = trim($body['food_name'] ?? '');
            if (!$name) err('food_name wajib diisi');

            // Coba exact match dulu, lalu LIKE sebagai fallback
            $stmt = $db->prepare("
                SELECT food_name, calories, unit
                FROM food_calories
                WHERE food_name = :name
                LIMIT 1
            ");
            $stmt->execute([':name' => $name]);
            $row = $stmt->fetch();

            if (!$row) {
                // Fallback: case-insensitive LIKE
                $stmt2 = $db->prepare("
                    SELECT food_name, calories, unit
                    FROM food_calories
                    WHERE LOWER(food_name) LIKE :name
                    LIMIT 1
                ");
                $stmt2->execute([':name' => '%' . strtolower($name) . '%']);
                $row = $stmt2->fetch();
            }

            if (!$row) err("Makanan '$name' tidak ditemukan di database", 404);
            ok($row);

        // ── Ambil semua kalori sebagai dict {nama: kalori} ──
        // Digunakan backend Python saat startup untuk cache lokal
        case 'get_calories_dict':
            $stmt = $db->query("
                SELECT food_name, calories FROM food_calories ORDER BY food_name ASC
            ");
            $rows = $stmt->fetchAll();
            // Ubah ke format {food_name: calories}
            $dict = [];
            foreach ($rows as $r) {
                $dict[$r['food_name']] = (int)$r['calories'];
            }
            ok($dict);

        // ── Tambah makanan baru ──────────────────────────────
        case 'add_food_calorie':
            $food_name = trim($body['food_name'] ?? '');
            $calories  = (int)($body['calories'] ?? 0);
            $unit      = trim($body['unit']      ?? 'per porsi');
            $notes     = trim($body['notes']     ?? '');
            if (!$food_name || $calories <= 0) err('food_name dan calories (> 0) wajib diisi');

            // Cek duplikat
            $chk = $db->prepare("SELECT id FROM food_calories WHERE LOWER(food_name) = LOWER(:n)");
            $chk->execute([':n' => $food_name]);
            if ($chk->fetch()) err("Makanan '$food_name' sudah ada di database");

            $db->prepare("
                INSERT INTO food_calories (food_name, calories, unit, notes)
                VALUES (:name, :cal, :unit, :notes)
            ")->execute([
                ':name'  => $food_name,
                ':cal'   => $calories,
                ':unit'  => $unit,
                ':notes' => $notes ?: null,
            ]);
            ok(['id' => $db->lastInsertId()], "Makanan '$food_name' ditambahkan");

        // ── Edit data kalori ─────────────────────────────────
        case 'edit_food_calorie':
            $id        = (int)($body['id']        ?? 0);
            $food_name = trim($body['food_name']  ?? '');
            $calories  = (int)($body['calories']  ?? 0);
            $unit      = trim($body['unit']       ?? 'per porsi');
            $notes     = trim($body['notes']      ?? '');
            if (!$id || !$food_name || $calories <= 0) err('id, food_name, dan calories wajib');

            // Cek duplikat nama (selain diri sendiri)
            $chk = $db->prepare("SELECT id FROM food_calories WHERE LOWER(food_name) = LOWER(:n) AND id != :id");
            $chk->execute([':n' => $food_name, ':id' => $id]);
            if ($chk->fetch()) err("Nama makanan '$food_name' sudah dipakai data lain");

            $db->prepare("
                UPDATE food_calories
                SET food_name = :name, calories = :cal, unit = :unit, notes = :notes
                WHERE id = :id
            ")->execute([
                ':name'  => $food_name,
                ':cal'   => $calories,
                ':unit'  => $unit,
                ':notes' => $notes ?: null,
                ':id'    => $id,
            ]);
            ok(null, "Data '$food_name' diperbarui");

        // ── Hapus data kalori ────────────────────────────────
        case 'delete_food_calorie':
            $id = (int)($body['id'] ?? 0);
            if (!$id) err('id wajib');
            $db->prepare("DELETE FROM food_calories WHERE id = :id")->execute([':id' => $id]);
            ok(null, 'Data kalori dihapus');

        // ── Statistik ringkas ─────────────────────────────────
        case 'stats':
            $row = $db->query("
                SELECT
                    (SELECT COUNT(*) FROM sessions) AS total_sessions,
                    (SELECT COUNT(*) FROM chat_history) AS total_messages,
                    (SELECT COUNT(*) FROM chat_history WHERE role='user') AS user_messages,
                    (SELECT COUNT(*) FROM iot_log) AS iot_commands,
                    (SELECT COUNT(*) FROM food_calories) AS food_items
            ")->fetch();
            ok($row);

        default:
            err("action '$action' tidak dikenal");
    }

} catch (PDOException $e) {
    err('Database error: ' . $e->getMessage(), 500);
} catch (Exception $e) {
    err('Server error: ' . $e->getMessage(), 500);
}