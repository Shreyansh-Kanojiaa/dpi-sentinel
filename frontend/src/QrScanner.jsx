import React, { useEffect, useRef, useState } from "react";
import jsQR from "jsqr";

// Reads a QR code off a printed certificate, using the device camera.
//
// This component knows NOTHING about certificates. Its only output is
// onText(decodedString) -> boolean. Every trust decision, including what a
// scanned string is allowed to do, belongs to the caller. That is deliberate:
// a scanned QR is attacker-controlled paper, and the component that talks to
// the camera must not also be the component that decides where to navigate.
// There is no code path from here to location, by construction.
//
// Two frame sources, one decoder:
//   - live preview, where the browser allows camera access
//   - a still photo, which is ALWAYS offered
//
// The photo input is not a fallback that capability detection switches to;
// it is always rendered. Detection only decides whether to additionally try
// the camera. That way a wrong guess, a declined permission or a webcam
// pointing at the ceiling can never leave someone with no way through.

// Module scope, not state: it is a property of the browser, and evaluating it
// once at import makes it fakeable in a test via addInitScript.
//
// On http://<LAN-IP>:5173 this is false, because navigator.mediaDevices is
// undefined outside a secure context. On http://localhost:5173 it is true.
const CAN_LIVE = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

const COPY = {
  insecure:
    "Live camera preview needs a secure (https) connection, and this page is being served over " +
    "plain http. Taking a photo of the code works exactly the same way, and the picture is read " +
    "here on your device.",
  noCameraApi:
    "This browser doesn't offer a live camera preview. Take a photo of the code instead, or type " +
    "the 32-character certificate id from the paper copy into the box above.",
  privacy:
    "The picture never leaves this device. Frames are read here in your browser to find the code, " +
    "and nothing is uploaded. The only thing that reaches the aggregator is the certificate id the " +
    "code contains.",
  noCodeInPhoto:
    "No QR code could be read in that photo. Fill more of the frame with the code, hold the paper " +
    "flat, and watch for glare from overhead light. You can also type the 32-character certificate " +
    "id printed beneath the code.",
  notACertificate: (text) =>
    "That code was read, but it doesn't refer to a DPI Sentinel certificate. It says: " +
    `${text.slice(0, 80)}${text.length > 80 ? "…" : ""}`,
};

function cameraNote(err) {
  switch (err?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Camera access was declined, so there's no live preview. You can allow it in your browser's site settings and open the scanner again, or take a photo of the code instead.";
    case "NotFoundError":
    case "DevicesNotFoundError":
      return "No camera was found on this device. Take a photo of the code with another device and pick the image here, or type the 32-character certificate id from the paper copy into the box above.";
    case "NotReadableError":
    case "TrackStartError":
      return "The camera is already in use by another app. Close it and open the scanner again, or take a photo of the code instead.";
    default:
      return "This device's camera couldn't be started for scanning. Take a photo of the code instead.";
  }
}

export default function QrScanner({ onText, onClose }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);   // created lazily, never added to the DOM
  const streamRef = useRef(null);   // so cleanup can always reach the tracks
  const timerRef = useRef(null);

  const [live, setLive] = useState(CAN_LIVE);
  const [note, setNote] = useState(null);   // { kind: "info" | "error", text }

  // Releasing the camera is the thing users notice when it goes wrong: the
  // indicator light staying on after the panel is gone. Called on decode, on
  // close, and on effect cleanup.
  const stop = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    // Safari keeps the indicator lit if the element still references the
    // stream, even after every track has been stopped.
    if (videoRef.current) videoRef.current.srcObject = null;
  };

  const handle = (text) => {
    if (text == null) {
      setNote({ kind: "error", text: COPY.noCodeInPhoto });
      return;
    }
    if (onText(text)) {
      stop();          // camera off BEFORE handing over, not after
      onClose();
      return;
    }
    // Read, but not ours. Live mode keeps looping underneath this.
    setNote({ kind: "error", text: COPY.notACertificate(text) });
  };

  const decodeFrom = (source, w, h, opts) => {
    const c = (canvasRef.current ??= document.createElement("canvas"));
    c.width = w;
    c.height = h;
    // Without willReadFrequently Chrome keeps the canvas GPU-backed and every
    // getImageData is a readback stall.
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(source, 0, 0, w, h);
    const img = ctx.getImageData(0, 0, w, h);
    return jsQR(img.data, img.width, img.height, opts);
  };

  const tick = () => {
    const v = videoRef.current;
    if (!v || v.readyState < 2 || !v.videoWidth) return;   // HAVE_CURRENT_DATA
    // dontInvert: a printed certificate is dark-on-light, and skipping the
    // inverted pass roughly halves the per-frame cost.
    // ponytail: fixed 5 fps, no downscale. If a 1080p webcam stutters, cap the
    // canvas at 640px wide.
    const hit = decodeFrom(v, v.videoWidth, v.videoHeight, { inversionAttempts: "dontInvert" });
    if (hit) handle(hit.data);
  };

  const onPhoto = async (e) => {
    const f = e.target.files?.[0];
    // Reset first: otherwise retaking an identically-named photo after a
    // failure fires no change event and the app looks broken.
    e.target.value = "";
    if (!f) return;
    setNote(null);
    try {
      const bmp = await createImageBitmap(f);
      // Phone cameras are 12MP; jsQR on that is slow enough to look hung and
      // can exhaust memory on a low-end handset. 1200px on the long edge
      // decodes any code that fills a reasonable part of the frame.
      // ponytail: single pass. If real photos start missing, retry once at
      // native resolution before widening this.
      const scale = Math.min(1, 1200 / Math.max(bmp.width, bmp.height));
      const hit = decodeFrom(bmp, Math.round(bmp.width * scale), Math.round(bmp.height * scale));
      bmp.close();
      handle(hit ? hit.data : null);
    } catch {
      handle(null);
    }
  };

  useEffect(() => {
    if (!live) return;
    let cancelled = false;

    (async () => {
      try {
        // "ideal", never "exact": exact throws OverconstrainedError on every
        // laptop, which is precisely where the webcam should just work.
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
        });
        // React 19 StrictMode runs mount -> cleanup -> mount in dev, so this
        // promise can resolve AFTER its own cleanup. Nothing else will ever
        // see this stream, so stop it here or the camera light stays on with
        // no visible UI attached to it.
        if (cancelled || !videoRef.current) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        videoRef.current.srcObject = stream;
        // Rejects with AbortError whenever srcObject is cleared mid-play,
        // which StrictMode does on every dev mount. Swallow it rather than
        // leaving an unhandled rejection in the console during a demo.
        await videoRef.current.play().catch(() => {});
        timerRef.current = setInterval(tick, 200);
      } catch (err) {
        if (cancelled) return;
        // getUserMedia rejects asynchronously, and the user may already have
        // picked a photo and got a result in the meantime. Never clobber a
        // more specific message with this one: whether the camera started is
        // the least interesting thing on screen once a code has been read.
        setNote((prev) => prev ?? { kind: "error", text: cameraNote(err) });
        setLive(false);   // fall back to the photo control, which is already there
      }
    })();

    return () => {
      cancelled = true;
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  return (
    <div className="qr-scanner">
      {live && <video ref={videoRef} className="qr-video" playsInline muted autoPlay />}
      {!live && <p className="qr-note">{CAN_LIVE ? COPY.noCameraApi : COPY.insecure}</p>}

      <label className="qr-photo">
        {live ? "…or take a photo of the code: " : "Take a photo of the code: "}
        <input type="file" accept="image/*" capture="environment" onChange={onPhoto} />
      </label>

      {note && (
        <div className={note.kind === "error" ? "copilot-error" : "qr-note"} role="status" aria-live="polite">
          {note.text}
        </div>
      )}

      <p className="qr-privacy">{COPY.privacy}</p>

      <button type="button" className="btn-ghost" onClick={() => { stop(); onClose(); }}>
        {live ? "Stop camera" : "Close"}
      </button>
    </div>
  );
}
