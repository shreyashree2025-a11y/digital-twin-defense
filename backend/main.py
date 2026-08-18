from __future__ import annotations

import os
import time
import shutil
import logging
import threading
from datetime import datetime, timezone
from typing import Generator, Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, UploadFile, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | SENTINEL | %(levelname)s | %(message)s",
)
logger = logging.getLogger("sentinel")

STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage")
os.makedirs(STORAGE_DIR, exist_ok=True)
UPLOADED_VIDEO_PATH = os.path.join(STORAGE_DIR, "current_upload.mp4")

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB safety cap

# Threat model thresholds — tunable "digital twin" risk parameters
DENSITY_ANOMALY_THRESHOLD = 35.0      # % of frame area in motion -> anomaly
VELOCITY_ANOMALY_THRESHOLD = 18.0     # frame-over-frame density delta -> anomaly
THREAT_SCORE_ANOMALY_THRESHOLD = 70.0
MIN_CONTOUR_AREA = 900                # px^2 — filters sensor noise / small blobs

JPEG_QUALITY = 80
TARGET_FPS = 20
FRAME_INTERVAL = 1.0 / TARGET_FPS

# --------------------------------------------------------------------------- #
# Shared analytics state (thread-safe, updated by whichever generator is
# actively streaming frames; read by /api/v1/analytics)
# --------------------------------------------------------------------------- #


class AnalyticsState:
    """Thread-safe container for the latest computed telemetry snapshot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data = {
            "density_percentage": 0.0,
            "threat_score": 0,
            "status": "STANDBY",
            "reasons": ["Awaiting active video source."],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_mode": "NONE",
            "fps": 0.0,
            "resolution": "0x0",
        }

    def update(self, **kwargs) -> None:
        with self._lock:
            self._data.update(kwargs)
            self._data["timestamp"] = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._data)


analytics_state = AnalyticsState()

# --------------------------------------------------------------------------- #
# Computer Vision Engine
# --------------------------------------------------------------------------- #


class ThreatVisionEngine:
    """
    Wraps an OpenCV MOG2 background subtractor and produces, per frame:
      - density_percentage : motion area relative to frame area
      - threat_score        : blended density + velocity risk metric
      - anomaly_detected    : boolean flag
      - reasons              : list of human-readable evidence strings
      - annotated frame      : HUD-painted BGR ndarray
    """

    def __init__(self) -> None:
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=40, detectShadows=True
        )
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._prev_density = 0.0
        self._ema_threat = 0.0

    def reset(self) -> None:
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=40, detectShadows=True
        )
        self._prev_density = 0.0
        self._ema_threat = 0.0

    def process(self, frame: np.ndarray) -> tuple[np.ndarray, dict]:
        height, width = frame.shape[:2]
        frame_area = float(height * width)

        # --- Background subtraction ---
        fg_mask = self.bg_subtractor.apply(frame)
        # Drop OpenCV's "shadow" gray value (127); keep hard foreground (255)
        _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self.kernel, iterations=1)
        fg_mask = cv2.dilate(fg_mask, self.kernel, iterations=2)

        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        significant_boxes = []
        motion_area = 0.0
        for c in contours:
            area = cv2.contourArea(c)
            if area < MIN_CONTOUR_AREA:
                continue
            motion_area += area
            x, y, w, h = cv2.boundingRect(c)
            significant_boxes.append((x, y, w, h))

        density_percentage = min(100.0, (motion_area / frame_area) * 100.0)

        # --- Velocity: frame-over-frame density delta acts as a motion-
        #     acceleration proxy (rapid crowd surge / dispersal detection) ---
        velocity = abs(density_percentage - self._prev_density)
        self._prev_density = density_percentage

        # --- Threat score: weighted blend of density + velocity, smoothed
        #     with an exponential moving average to avoid flicker ---
        raw_threat = min(100.0, (density_percentage * 0.75) + (velocity * 1.5))
        self._ema_threat = (self._ema_threat * 0.6) + (raw_threat * 0.4)
        threat_score = int(round(min(100.0, self._ema_threat)))

        reasons: list[str] = []
        if density_percentage > DENSITY_ANOMALY_THRESHOLD:
            reasons.append(
                f"Critical crowd density threshold exceeded ({density_percentage:.1f}% > "
                f"{DENSITY_ANOMALY_THRESHOLD:.0f}%)"
            )
        if velocity > VELOCITY_ANOMALY_THRESHOLD:
            reasons.append(
                f"High optical motion variance detected (Δ{velocity:.1f}%/frame)"
            )
        if len(significant_boxes) >= 6:
            reasons.append(
                f"Elevated number of discrete moving targets ({len(significant_boxes)})"
            )
        if threat_score > THREAT_SCORE_ANOMALY_THRESHOLD:
            reasons.append(f"Composite threat index in critical band ({threat_score}%)")

        anomaly_detected = bool(
            density_percentage > DENSITY_ANOMALY_THRESHOLD
            or velocity > VELOCITY_ANOMALY_THRESHOLD
            or threat_score > THREAT_SCORE_ANOMALY_THRESHOLD
        )
        if not reasons:
            reasons.append("Nominal ambient motion within baseline parameters.")

        status_label = "ANOMALY" if anomaly_detected else "NORMAL"

        annotated = self._draw_hud(
            frame,
            boxes=significant_boxes,
            density=density_percentage,
            threat=threat_score,
            status_label=status_label,
        )

        payload = {
            "density_percentage": round(density_percentage, 2),
            "threat_score": threat_score,
            "status": status_label,
            "anomaly_detected": anomaly_detected,
            "reasons": reasons,
        }
        return annotated, payload

    @staticmethod
    def _draw_hud(
        frame: np.ndarray,
        boxes: list[tuple[int, int, int, int]],
        density: float,
        threat: int,
        status_label: str,
    ) -> np.ndarray:
        h, w = frame.shape[:2]
        out = frame.copy()

        is_anomaly = status_label == "ANOMALY"
        primary_color = (60, 60, 240) if is_anomaly else (140, 220, 90)  # BGR
        accent_color = (255, 220, 120)

        # --- Target bounding boxes ---
        for (x, y, bw, bh) in boxes:
            cv2.rectangle(out, (x, y), (x + bw, y + bh), primary_color, 2)
            cv2.putText(
                out,
                "TGT",
                (x, max(0, y - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                primary_color,
                1,
                cv2.LINE_AA,
            )

        # --- Full-frame status border ---
        border_thickness = 6
        cv2.rectangle(out, (0, 0), (w - 1, h - 1), primary_color, border_thickness)

        # --- Top HUD strip (semi-transparent) ---
        overlay = out.copy()
        cv2.rectangle(overlay, (0, 0), (w, 58), (10, 12, 18), -1)
        out = cv2.addWeighted(overlay, 0.55, out, 0.45, 0)

        cv2.putText(
            out,
            f"STATUS: {status_label}",
            (16, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            primary_color,
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            out,
            "SENTINEL DIGITAL TWIN // LIVE VISION ENGINE",
            (16, 46),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (170, 190, 200),
            1,
            cv2.LINE_AA,
        )

        # --- Threat badge, top-right ---
        badge_text = f"THREAT {threat}%"
        (tw, th), _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        badge_x2 = w - 14
        badge_x1 = badge_x2 - tw - 24
        cv2.rectangle(out, (badge_x1, 12), (badge_x2, 12 + th + 18), primary_color, -1)
        cv2.putText(
            out,
            badge_text,
            (badge_x1 + 12, 12 + th + 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (10, 10, 10),
            2,
            cv2.LINE_AA,
        )

        # --- Density readout, bottom-left ---
        density_text = f"DENSITY {density:.1f}%"
        cv2.putText(
            out,
            density_text,
            (16, h - 16),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            accent_color,
            2,
            cv2.LINE_AA,
        )

        # --- Timestamp, bottom-right ---
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        (tsw, tsh), _ = cv2.getTextSize(ts, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
        cv2.putText(
            out,
            ts,
            (w - tsw - 14, h - 16),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (150, 160, 170),
            1,
            cv2.LINE_AA,
        )

        return out


# --------------------------------------------------------------------------- #
# Frame generators (MJPEG multipart streams)
# --------------------------------------------------------------------------- #


def _encode_jpeg(frame: np.ndarray) -> Optional[bytes]:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        return None
    return buf.tobytes()


def _mjpeg_boundary(jpeg_bytes: bytes) -> bytes:
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n\r\n"
        + jpeg_bytes + b"\r\n"
    )


def generate_uploaded_video_frames() -> Generator[bytes, None, None]:
    """Streams the last-uploaded video file through the vision engine, looping
    continuously so the "digital twin" feed never goes dark."""
    if not os.path.exists(UPLOADED_VIDEO_PATH):
        analytics_state.update(
            status="ERROR",
            reasons=["No video file has been uploaded yet."],
            source_mode="FILE_UPLOAD",
        )
        blank = np.zeros((480, 854, 3), dtype=np.uint8)
        cv2.putText(
            blank,
            "NO SOURCE VIDEO UPLOADED",
            (140, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (90, 90, 200),
            2,
            cv2.LINE_AA,
        )
        jpeg = _encode_jpeg(blank)
        if jpeg:
            yield _mjpeg_boundary(jpeg)
        return

    engine = ThreatVisionEngine()

    while True:
        cap = cv2.VideoCapture(UPLOADED_VIDEO_PATH)
        if not cap.isOpened():
            logger.error("Failed to open uploaded video: %s", UPLOADED_VIDEO_PATH)
            analytics_state.update(status="ERROR", reasons=["Could not open source video file."])
            return

        src_fps = cap.get(cv2.CAP_PROP_FPS) or TARGET_FPS
        frame_interval = 1.0 / src_fps if src_fps > 0 else FRAME_INTERVAL
        engine.reset()

        while True:
            loop_start = time.time()
            ok, frame = cap.read()
            if not ok:
                break  # end of clip -> outer loop reopens and restarts

            annotated, payload = engine.process(frame)
            h, w = annotated.shape[:2]
            elapsed = time.time() - loop_start
            instant_fps = 1.0 / elapsed if elapsed > 0 else src_fps

            analytics_state.update(
                density_percentage=payload["density_percentage"],
                threat_score=payload["threat_score"],
                status=payload["status"],
                reasons=payload["reasons"],
                source_mode="FILE_UPLOAD",
                fps=round(min(instant_fps, src_fps), 1),
                resolution=f"{w}x{h}",
            )

            jpeg = _encode_jpeg(annotated)
            if jpeg:
                yield _mjpeg_boundary(jpeg)

            sleep_for = frame_interval - (time.time() - loop_start)
            if sleep_for > 0:
                time.sleep(sleep_for)

        cap.release()


def generate_live_camera_frames(device_index: int = 0) -> Generator[bytes, None, None]:
    """Streams the local webcam (device 0) through the vision engine."""
    cap = cv2.VideoCapture(device_index)
    if not cap.isOpened():
        logger.error("Failed to open camera device index %s", device_index)
        analytics_state.update(
            status="ERROR",
            reasons=[f"Unable to access camera device index {device_index}."],
            source_mode="LIVE_CAMERA",
        )
        blank = np.zeros((480, 854, 3), dtype=np.uint8)
        cv2.putText(
            blank,
            "CAMERA DEVICE UNAVAILABLE",
            (150, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (90, 90, 200),
            2,
            cv2.LINE_AA,
        )
        jpeg = _encode_jpeg(blank)
        if jpeg:
            yield _mjpeg_boundary(jpeg)
        return

    engine = ThreatVisionEngine()

    try:
        while True:
            loop_start = time.time()
            ok, frame = cap.read()
            if not ok:
                logger.warning("Camera read failed; retrying...")
                time.sleep(0.25)
                continue

            annotated, payload = engine.process(frame)
            h, w = annotated.shape[:2]
            elapsed = time.time() - loop_start
            instant_fps = 1.0 / elapsed if elapsed > 0 else TARGET_FPS

            analytics_state.update(
                density_percentage=payload["density_percentage"],
                threat_score=payload["threat_score"],
                status=payload["status"],
                reasons=payload["reasons"],
                source_mode="LIVE_CAMERA",
                fps=round(min(instant_fps, TARGET_FPS * 1.5), 1),
                resolution=f"{w}x{h}",
            )

            jpeg = _encode_jpeg(annotated)
            if jpeg:
                yield _mjpeg_boundary(jpeg)

            sleep_for = FRAME_INTERVAL - (time.time() - loop_start)
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        cap.release()


# --------------------------------------------------------------------------- #
# FastAPI application
# --------------------------------------------------------------------------- #

app = FastAPI(
    title="SENTINEL Digital Twin Defense Platform",
    description="Computer-vision powered crowd density & threat telemetry engine.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/health", tags=["system"])
def health_check() -> dict:
    return {"status": "ok", "service": "sentinel-vision-engine", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/api/v1/upload-video", tags=["ingestion"], status_code=status.HTTP_201_CREATED)
async def upload_video(file: UploadFile = File(...)) -> JSONResponse:
    """Accepts a multipart video upload and persists it to temporary storage
    so it can be consumed by GET /api/v1/video-feed."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file extension '{ext}'. Allowed: {sorted(ALLOWED_VIDEO_EXTENSIONS)}",
        )

    try:
        size = 0
        with open(UPLOADED_VIDEO_PATH, "wb") as out_file:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE_BYTES:
                    out_file.close()
                    os.remove(UPLOADED_VIDEO_PATH)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="File exceeds maximum allowed size of 500MB.",
                    )
                out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Video upload failed")
        raise HTTPException(status_code=500, detail=f"Failed to store video: {exc}") from exc
    finally:
        await file.close()

    logger.info("Stored uploaded video (%s bytes) at %s", size, UPLOADED_VIDEO_PATH)
    analytics_state.update(
        status="STANDBY",
        reasons=["New source video ingested. Awaiting stream activation."],
        source_mode="FILE_UPLOAD",
    )

    return JSONResponse(
        {
            "message": "Video uploaded successfully.",
            "filename": file.filename,
            "size_bytes": size,
            "stream_endpoint": "/api/v1/video-feed",
        }
    )


@app.get("/api/v1/video-feed", tags=["streaming"])
def video_feed() -> StreamingResponse:
    """Streams the previously uploaded video through the CV engine as MJPEG."""
    return StreamingResponse(
        generate_uploaded_video_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/v1/live-feed", tags=["streaming"])
def live_feed() -> StreamingResponse:
    """Streams local webcam device 0 through the CV engine as MJPEG."""
    return StreamingResponse(
        generate_live_camera_frames(0),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/v1/analytics", tags=["telemetry"])
def analytics() -> JSONResponse:
    """Returns the latest computed telemetry snapshot for the dashboard."""
    snap = analytics_state.snapshot()
    return JSONResponse(
        {
            "density_percentage": snap["density_percentage"],
            "threat_score": snap["threat_score"],
            "status": snap["status"],
            "reasons": snap["reasons"],
            "timestamp": snap["timestamp"],
            "source_mode": snap["source_mode"],
            "fps": snap["fps"],
            "resolution": snap["resolution"],
        }
    )


@app.on_event("shutdown")
def cleanup_storage() -> None:
    try:
        if os.path.isdir(STORAGE_DIR):
            shutil.rmtree(STORAGE_DIR, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)