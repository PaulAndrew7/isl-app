#!/usr/bin/env python3
import argparse
import os
import sys
import time
import uuid
import shutil
import glob
from typing import Tuple
from faster_whisper import WhisperModel
import yt_dlp

def srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def write_srt(segments, out_path: str) -> None:
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = srt_timestamp(seg["start"])
        end = srt_timestamp(seg["end"])
        text = (seg["text"] or "").strip()
        lines += [str(i), f"{start} --> {end}", text, ""]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def download_audio_from_youtube(url: str, session_dir: str) -> Tuple[str, str]:
    """
    Download best audio and convert to MP3 using yt-dlp+ffmpeg.
    Returns (audio_mp3_path, video_title).
    """
    os.makedirs(session_dir, exist_ok=True)

    # First, get metadata (for title / id if you want)
    with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    title = info.get("title") or info.get("id") or "audio"

    # Use video ID as base name to avoid weird characters in filenames
    outtmpl = os.path.join(session_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "restrictfilenames": True,  # safer filenames
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
        "quiet": True,
        "noplaylist": True,
        "ignoreerrors": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # Find the resulting MP3 (there should be exactly one with that id)
    vid = info.get("id")
    expected = os.path.join(session_dir, f"{vid}.mp3") if vid else None
    if expected and os.path.exists(expected):
        return expected, title

    # Fallback: pick the newest .mp3 in the session dir
    mp3s = sorted(glob.glob(os.path.join(session_dir, "*.mp3")), key=os.path.getmtime, reverse=True)
    if not mp3s:
        raise RuntimeError(
            "yt-dlp did not produce an MP3. Make sure ffmpeg is installed and on PATH."
        )
    return mp3s[0], title

def transcribe_with_whisper(audio_path: str, model_name: str, device: str, compute_type: str, beam_size: int, vad: bool):
    print(f"[whisper] loading model='{model_name}' device='{device}' compute_type='{compute_type}'...")
    t0 = time.time()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    print(f"[whisper] model loaded in {time.time()-t0:.2f}s")

    print(f"[whisper] transcribing: {audio_path}")
    t1 = time.time()
    segments_iter, info = model.transcribe(
        audio_path,
        beam_size=beam_size,
        vad_filter=vad,
    )
    segments = [{"start": s.start, "end": s.end, "text": s.text} for s in segments_iter]
    dur = time.time() - t1
    lang = getattr(info, "language", "?")
    total = getattr(info, "duration", "?")
    print(f"[whisper] done in {dur:.2f}s | language={lang} | audio_duration={total}s")
    return segments, info

def main():
    ap = argparse.ArgumentParser(description="Download YouTube audio with yt-dlp and transcribe with faster-whisper.")
    ap.add_argument("url", help="YouTube video URL")
    ap.add_argument("-o", "--outdir", default="temp", help="Output directory (default: temp)")
    ap.add_argument("--model", default="small", help="Whisper model: tiny/base/small/medium/large-v3 (default: small)")
    ap.add_argument("--device", default="cpu", help="Device: cpu or cuda (default: cpu)")
    ap.add_argument("--compute-type", default="int8", help="int8/int8_float16/float16/float32 (default: int8)")
    ap.add_argument("--beam-size", type=int, default=5, help="Beam size (default: 5)")
    ap.add_argument("--vad", action="store_true", help="Enable VAD filter")
    ap.add_argument("--clean", action="store_true", help="Clean output directory before running")
    args = ap.parse_args()

    # Prepare session dir
    os.makedirs(args.outdir, exist_ok=True)
    if args.clean:
        for entry in os.listdir(args.outdir):
            path = os.path.join(args.outdir, entry)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
            except Exception:
                pass

    session_id = str(uuid.uuid4())
    session_dir = os.path.join(args.outdir, session_id)
    os.makedirs(session_dir, exist_ok=True)

    # 1) Download audio
    print("[1/3] Downloading audio with yt-dlp…")
    audio_path, title = download_audio_from_youtube(args.url, session_dir)
    print(f"[1/3] Audio: {audio_path}")

    # 2) Transcribe with faster-whisper
    print("[2/3] Transcribing with faster-whisper…")
    segments, info = transcribe_with_whisper(
        audio_path,
        model_name=args.model,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
        vad=args.vad,
    )

    # 3) Write SRT
    print("[3/3] Writing SRT…")
    # base the srt name on YouTube ID (from audio filename) or title
    base = os.path.splitext(os.path.basename(audio_path))[0]  # e.g., VIDEOID
    srt_path = os.path.join(session_dir, f"{base}.whisper.srt")
    write_srt(segments, srt_path)

    # preview
    print("\n--- first few lines ---")
    for i, seg in enumerate(segments[:5], start=1):
        print(f"{i:02d}. [{srt_timestamp(seg['start'])} → {srt_timestamp(seg['end'])}] {seg['text']}")

    print("\nDone ✅")
    print(f"SRT: {srt_path}")
    print(f"Session: {session_dir}")
    print(f"Title: {title}")

if __name__ == "__main__":
    main()
