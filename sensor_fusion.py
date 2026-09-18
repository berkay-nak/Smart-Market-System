import sys
from pathlib import Path
import cv2
import numpy as np
import time
import torch
import torchvision
from torchvision.models.detection import keypointrcnn_resnet50_fpn
from torchvision.transforms import functional as F
import threading
import socket
from collections import deque
from database_manager import MarketDatabase
from boxmot import StrongSort
import boxmot.trackers.strongsort.sort.linear_assignment as _ss_la

# ============================================================
# MONKEY-PATCH: Gating'i sadece pozisyon (x,y) bazlı yap.
# Böylece kol kaldırma (aspect ratio değişimi) VE
# farklı pozisyondan giriş (büyük konum farkı) gating'i bozmaz.
# ============================================================
_orig_gate = _ss_la.gate_cost_matrix
def _position_only_gate(cost_matrix, tracks, detections, track_indices,
                        detection_indices, mc_lambda, gated_cost=_ss_la.INFTY_COST,
                        only_position=False):
    return _orig_gate(cost_matrix, tracks, detections, track_indices,
                      detection_indices, mc_lambda, gated_cost, only_position=True)
_ss_la.gate_cost_matrix = _position_only_gate

S1_PRODUCT_WEIGHT = 140.0
S2_PRODUCT_WEIGHT = 200.0
WEIGHT_TOLERANCE = 10.0
NOISE_THRESHOLD = 8.0


class ChannelState:
    def __init__(self, name: str):
        self.name = name
        self.MEDIAN_N = 2
        self.LEVEL_N = 3
        self.LEVEL_RANGE_TH = 4.0
        self.START_DEV_TH = 6.0
        self.START_DG_TH = 3.0
        self.START_CONSEC = 2
        self.MIN_EVENT_SAMPLES = 2
        self.MIN_DELTA_TH = 5.0

        self.raw_buf = deque(maxlen=self.MEDIAN_N)
        self.level_buf = deque(maxlen=self.LEVEL_N)

        self.stable_level = None
        self.in_event = False
        self.event_vals = []
        self.level_at_start = None
        self.event_start_time = 0.0

        self.current_val = 0.0
        self.last_event_data = None
        self.has_new_event = False
        self.prev_f = None
        self.start_cnt = 0

    def median(self, xs):
        s = sorted(xs)
        return s[len(s) // 2]

    def mean(self, xs):
        return sum(xs) / len(xs)

    def stable(self, buf):
        return (max(buf) - min(buf)) < self.LEVEL_RANGE_TH

    def process(self, g: float, t: float):
        self.current_val = g

        self.raw_buf.append(g)
        if len(self.raw_buf) < self.MEDIAN_N:
            return
        f = self.median(list(self.raw_buf))

        if self.stable_level is None:
            self.stable_level = f
            self.prev_f = f
            self.level_buf.clear()
            return

        dg = f - self.prev_f
        self.prev_f = f
        dev = f - self.stable_level

        if not self.in_event:
            cond = (abs(dev) > self.START_DEV_TH) or (abs(dg) > self.START_DG_TH)
            self.start_cnt = self.start_cnt + 1 if cond else 0

            if self.start_cnt >= self.START_CONSEC:
                self.in_event = True
                self.level_at_start = self.stable_level
                self.event_start_time = time.time()
                self.event_vals = [f]
                self.level_buf.clear()
                self.start_cnt = 0
            else:
                self.level_buf.append(f)
                if len(self.level_buf) == self.LEVEL_N and self.stable(self.level_buf):
                    self.stable_level = self.mean(self.level_buf)
            return

        self.event_vals.append(f)
        self.level_buf.append(f)

        if len(self.level_buf) == self.LEVEL_N and self.stable(self.level_buf) and len(self.event_vals) >= self.MIN_EVENT_SAMPLES:
            new_level = self.mean(self.level_buf)
            delta = new_level - self.level_at_start

            if abs(delta) < self.MIN_DELTA_TH:
                self.stable_level = new_level
                self.in_event = False
                self.event_vals = []
                self.level_buf.clear()
                return

            kind = "PUTBACK" if delta > 0 else "PICKUP"
            amount = abs(delta)

            self.last_event_data = {
                "name": self.name,
                "type": kind,
                "amount": amount,
                "start_time": self.event_start_time,
                "time": time.time()
            }
            self.has_new_event = True

            print(f"<<< {self.name} ALGILANDI: {kind} ({amount:.2f}g değişim)")

            self.stable_level = new_level
            self.in_event = False
            self.event_vals = []
            self.level_buf.clear()


class LoadCellWorker(threading.Thread):
    def __init__(self, udp_ip="0.0.0.0", udp_port=12345):
        super().__init__()
        self.udp_ip = udp_ip
        self.udp_port = udp_port
        self.daemon = True
        self.running = True

        self.ch1 = ChannelState("S1")
        self.ch2 = ChannelState("S2")

    def run(self):
        print(f"[Sistem] UDP Soketi açılıyor: Port {self.udp_port} dinleniyor...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((self.udp_ip, self.udp_port))
            sock.settimeout(1.0)

            while self.running:
                try:
                    data, addr = sock.recvfrom(1024)
                    line = data.decode('utf-8').strip()

                    parts = line.split(',')
                    if len(parts) == 2:
                        g1 = float(parts[0])
                        g2 = float(parts[1])
                        t = time.perf_counter()
                        self.ch1.process(g1, t)
                        self.ch2.process(g2, t)

                except socket.timeout:
                    continue
                except ValueError:
                    continue
                except Exception as e:
                    pass

        except Exception as e:
            print(f"UDP Başlatma Hatası: {e}")
        finally:
            if 'sock' in locals():
                sock.close()


def calculate_items(weight_delta, product_weight):
    if weight_delta < NOISE_THRESHOLD:
        return 0, "NOISE"

    qty = int(round(weight_delta / product_weight))

    if qty == 0:
        return 1, "FAULTY"

    expected_weight = qty * product_weight
    diff = abs(weight_delta - expected_weight)

    if diff <= (WEIGHT_TOLERANCE * qty):
        return qty, "VALID"
    else:
        return qty, "FAULTY"


load_cell = LoadCellWorker(udp_ip="0.0.0.0", udp_port=12345)
load_cell.start()

shelf1_box = [434, 400, 615, 670]
shelf2_box = [600, 400, 849, 665]

device_str = '0' if torch.cuda.is_available() else 'cpu'
half_precision = (device_str == '0')


weights = torchvision.models.detection.KeypointRCNN_ResNet50_FPN_Weights.DEFAULT
model = keypointrcnn_resnet50_fpn(weights=weights).eval().to(device_str if device_str == 'cpu' else 'cuda')

tracker = StrongSort(
    reid_weights=Path('osnet_x1_0_msmt17.pt'),
    device=device_str,
    half=half_precision,

    # 1. ALGILAMA EŞİKLERİ
    det_thresh=0.35,
    min_conf=0.35,
    min_hits=2,
    n_init=3,

    iou_threshold=0.50,

    # 2. HAFIZA VE TRACK ÖMRÜ
    max_age=150,
    max_obs=160,

    # 3. ReID (GÖRÜNÜM) EŞLEŞTİRME
    max_cos_dist=0.70,
    nn_budget=100,

    # 4. APPEARANCE vs MOTION DENGESİ
    mc_lambda=0.985,
    ema_alpha=0.65,    # Düşük = hızlı adaptasyon. Yandan/önden dönüşte feature hızla güncellenir.

    # 5. IoU EŞLEŞME ESNEKLİĞİ
    max_iou_dist=0.80,
)

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1080)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1920)


def compute_stable_crop_box(kps, img_h, img_w, fallback_box):
    """
    Sadece ReID feature çıkarmak için gövde crop kutusu hesaplar.
    Kollar DAHİL EDİLMEZ. Kalman tracking'e gitmez, sadece ReID'ye gider.
    """
    xs, ys = [], []
    for idx in [5, 6, 11, 12]:  # omuzlar + kalçalar
        if kps[idx][2] > 0.4:
            xs.append(float(kps[idx][0]))
            ys.append(float(kps[idx][1]))
    for idx in [0, 1, 2, 3, 4]:  # baş
        if kps[idx][2] > 0.4:
            xs.append(float(kps[idx][0]))
            ys.append(float(kps[idx][1]))

    if len(xs) < 3:
        return fallback_box

    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    sx1 = max(0, min(xs) - span_x * 0.3)
    sy1 = max(0, min(ys) - span_y * 0.1)
    sx2 = min(img_w, max(xs) + span_x * 0.3)
    sy2 = min(img_h, max(ys) + span_y * 0.1)

    if (sx2 - sx1) > 20 and (sy2 - sy1) > 40:
        return [sx1, sy1, sx2, sy2]
    return fallback_box


def get_color(track_id):
    np.random.seed(int(track_id))
    return tuple(np.random.randint(0, 255, 3).tolist())


history_s1 = {}
history_s2 = {}
messages = []
db = MarketDatabase()
print(
    f"\nSİSTEM AKTİF! (S1: {S1_PRODUCT_WEIGHT}g | S2: {S2_PRODUCT_WEIGHT}g | Tol: ±{WEIGHT_TOLERANCE}g | Min Algılama: {NOISE_THRESHOLD}g)"
)
prev_frame_time = 0
new_frame_time = 0
while True:
    ret, frame = cap.read()
    if not ret:
        break

    img_tensor = F.to_tensor(frame).to(device_str if device_str == 'cpu' else 'cuda')
    with torch.no_grad():
        outputs = model([img_tensor])

    mask = (outputs[0]['scores'] > 0.8) & (outputs[0]['labels'] == 1)
    filtered_boxes = outputs[0]['boxes'][mask]
    filtered_scores = outputs[0]['scores'][mask]
    filtered_kps = outputs[0]['keypoints'][mask]

    # --- ORİJİNAL kutularla tracking, stabil crop ile ReID ---
    dets_to_track = []
    reid_crop_boxes = []   # ReID feature çıkarmak için gövde crop kutuları
    det_kp_map = []
    if len(filtered_boxes) > 0:
        fb_np = filtered_boxes.cpu().numpy()
        fs_np = filtered_scores.cpu().numpy()
        fk_np = filtered_kps.cpu().numpy()
        img_h, img_w = frame.shape[:2]
        for i, box in enumerate(fb_np):
            x1, y1, x2, y2 = box
            if (x2 - x1) * (y2 - y1) > 10000:
                dets_to_track.append([x1, y1, x2, y2, fs_np[i], 0])
                reid_crop_boxes.append(compute_stable_crop_box(fk_np[i], img_h, img_w, [x1, y1, x2, y2]))
                det_kp_map.append(i)

    dets_to_track = np.array(dets_to_track)

    # ReID feature'ları stabil gövde crop'undan çıkar (kol bağımsız)
    # AMA tracking kutusu orijinal kalır (Kalman smooth takip)
    stable_embs = None
    if len(dets_to_track) > 0 and len(reid_crop_boxes) > 0:
        stable_embs = tracker.model.get_features(np.array(reid_crop_boxes), frame)

    tracks = tracker.update(dets_to_track, frame, embs=stable_embs) if len(dets_to_track) > 0 else np.empty((0, 8))

    # --- DUPLİKAT TRACK BASKILAMA ---
    # Aynı kişi için iki track varsa (ör. önden=ID4, yandan=ID1),
    # Kalman tahminleri aynı yerde olur. IoU yüksekse zayıf olanı sil.
    confirmed = [t for t in tracker.tracker.tracks if t.is_confirmed()]
    if len(confirmed) >= 2:
        to_delete = set()
        for i in range(len(confirmed)):
            if id(confirmed[i]) in to_delete:
                continue
            bi = confirmed[i].to_tlbr()
            for j in range(i + 1, len(confirmed)):
                if id(confirmed[j]) in to_delete:
                    continue
                bj = confirmed[j].to_tlbr()
                inter = max(0, min(bi[2], bj[2]) - max(bi[0], bj[0])) * \
                        max(0, min(bi[3], bj[3]) - max(bi[1], bj[1]))
                area_i = (bi[2] - bi[0]) * (bi[3] - bi[1])
                area_j = (bj[2] - bj[0]) * (bj[3] - bj[1])
                union = area_i + area_j - inter
                iou = inter / union if union > 0 else 0
                if iou > 0.3:
                    # Daha az hit alan track'i öldür
                    loser = confirmed[j] if confirmed[i].hits >= confirmed[j].hits else confirmed[i]
                    loser.state = 3  # TrackState.Deleted
                    to_delete.add(id(loser))
        # Silinen track'leri listeden temizle
        tracker.tracker.tracks = [t for t in tracker.tracker.tracks if not t.is_deleted()]

    if len(tracks) > 0:
        for track in tracks:
            tx1, ty1, tx2, ty2, track_id, conf, cls, orig_idx = track.astype('int')[:8]

            kp_idx = det_kp_map[orig_idx] if orig_idx < len(det_kp_map) else -1
            if 0 <= kp_idx < len(fk_np):
                kp_data = fk_np[kp_idx]
                for p_idx in [9, 10]:
                    x, y, v = kp_data[p_idx]
                    x, y = int(x), int(y)
                    if v > 0.6:
                        if (shelf1_box[0] < x < shelf1_box[2]) and (shelf1_box[1] < y < shelf1_box[3]):
                            history_s1[track_id] = time.time()
                            cv2.circle(frame, (x, y), 8, (0, 255, 255), -1)
                        elif (shelf2_box[0] < x < shelf2_box[2]) and (shelf2_box[1] < y < shelf2_box[3]):
                            history_s2[track_id] = time.time()
                            cv2.circle(frame, (x, y), 8, (255, 0, 255), -1)
                        else:
                            cv2.circle(frame, (x, y), 5, (0, 255, 0), -1)

            col = get_color(track_id)
            cv2.rectangle(frame, (tx1, ty1), (tx2, ty2), col, 2)
            cv2.putText(frame, f"ID:{track_id}", (tx1, ty1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)

    cur_time = time.time()

    # Bellek temizleme: eski kayıtları sil
    history_s1 = {k: v for k, v in history_s1.items() if cur_time - v < 3.0}
    history_s2 = {k: v for k, v in history_s2.items() if cur_time - v < 3.0}

    def process_smart_event(channel, history_dict, shelf_name):
        evt = channel.last_event_data
        pw = S1_PRODUCT_WEIGHT if shelf_name == "RULO_HAVLU" else S2_PRODUCT_WEIGHT
        qty, status = calculate_items(evt['amount'], pw)

        if status == "NOISE":
            channel.has_new_event = False
            return

        # Ağırlığın DÜŞMEYE BAŞLADIĞI anı baz al (SENSOR_DELAY artık gereksiz)
        action_start_time = evt['start_time']
        best_id = "?"
        min_diff = 1.0  # Keskin eşleşme toleransı (1 saniye)

        for t_id, last_seen in history_dict.items():
            time_diff = abs(action_start_time - last_seen)
            if time_diff <= min_diff:
                min_diff = time_diff
                best_id = t_id

        if best_id == "?":
            display_msg = f"{shelf_name}: KIMSE YOK -> HATALI ({evt['amount']:.0f}g)"
            col = (0, 165, 255)
            print(f">>> ALARM: {display_msg}")
            messages.append({"text": display_msg, "time": cur_time, "col": col})
            channel.has_new_event = False
            return

        if status == "FAULTY":
            display_msg = f"{shelf_name}: ID {best_id} -> HATALI ({evt['amount']:.0f}g)"
            col = (0, 165, 255)
            print(f">>> ALARM: {display_msg}")
        else:
            action = "ALDI" if evt['type'] == 'PICKUP' else "BIRAKTI"
            col = (0, 255, 0) if action == "ALDI" else (0, 0, 255)
            display_msg = f"{shelf_name}: ID {best_id} -> {qty} ADET {action}"
            print(f">>> ONAY: {display_msg}")

            db_raf_ismi = "RAF_1" if shelf_name == "RULO_HAVLU" else "RAF_2"

            db.process_transaction(
                shelf_name=db_raf_ismi,
                customer_id=f"ID_{best_id}",
                action_type=evt['type'],
                quantity=qty
            )

        messages.append({"text": display_msg, "time": cur_time, "col": col})
        channel.has_new_event = False

    if load_cell.ch1.has_new_event:
        process_smart_event(load_cell.ch1, history_s1, "RULO_HAVLU")
    if load_cell.ch2.has_new_event:
        process_smart_event(load_cell.ch2, history_s2, "SU")

    cv2.rectangle(frame, (shelf1_box[0], shelf1_box[1]), (shelf1_box[2], shelf1_box[3]), (0, 255, 255), 2)
    cv2.putText(frame, f"S1: {load_cell.ch1.current_val:.0f}g", (shelf1_box[0], shelf1_box[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    cv2.rectangle(frame, (shelf2_box[0], shelf2_box[1]), (shelf2_box[2], shelf2_box[3]), (255, 0, 255), 2)
    cv2.putText(frame, f"S2: {load_cell.ch2.current_val:.0f}g", (shelf2_box[0], shelf2_box[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)

    y_off = 50
    keep_msgs = []
    for m in messages:
        if cur_time - m["time"] < 6.0:
            (w, h), _ = cv2.getTextSize(m["text"], cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
            cv2.rectangle(frame, (45, y_off - 25), (50 + w + 10, y_off + 10), (40, 40, 40), -1)
            cv2.putText(frame, m["text"], (50, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.7, m["col"], 2)
            y_off += 45
            keep_msgs.append(m)
    messages = keep_msgs
    new_frame_time = time.time()
    fps = 1 / (new_frame_time - prev_frame_time)
    prev_frame_time = new_frame_time
    fps = int(fps)
    cv2.putText(frame, f"FPS: {fps}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (100, 255, 0), 2)
    cv2.imshow("Smart Market", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

load_cell.running = False
cap.release()
cv2.destroyAllWindows()