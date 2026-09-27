/* Voice-note recorder for the order page: records the microphone with the Web
   Audio API and encodes a mono 16 kHz WAV — a format every phone plays (Chrome's
   MediaRecorder only makes WebM, which iPhones can't). Hard stop at 30 s. */
(function () {
  "use strict";
  var MAX = 30, RATE = 16000;
  var startBtn = document.getElementById("rec-start");
  if (!startBtn) return;
  var stopBtn = document.getElementById("rec-stop"), time = document.getElementById("rec-time"),
      preview = document.getElementById("rec-preview"), form = document.getElementById("rec-form"),
      fileInput = document.getElementById("rec-file"), secondsInput = document.getElementById("rec-seconds"),
      help = document.getElementById("rec-help");
  var ctx, stream, source, processor, chunks = [], inRate = 44100, started = 0, timer;

  function stopAll() {
    clearInterval(timer);
    if (processor) processor.disconnect();
    if (source) source.disconnect();
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    if (ctx) ctx.close();
    startBtn.hidden = false; stopBtn.hidden = true;
  }

  function downsample(buffer, from, to) {
    if (from === to) return buffer;
    var ratio = from / to, out = new Float32Array(Math.floor(buffer.length / ratio));
    for (var i = 0; i < out.length; i++) {
      var start = Math.floor(i * ratio), end = Math.floor((i + 1) * ratio), sum = 0, n = 0;
      for (var j = start; j < end && j < buffer.length; j++) { sum += buffer[j]; n++; }
      out[i] = n ? sum / n : 0;
    }
    return out;
  }

  function wav(samples) {
    var buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
    function str(o, s) { for (var i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); }
    str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, RATE, true);
    v.setUint32(28, RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data");
    v.setUint32(40, samples.length * 2, true);
    for (var i = 0; i < samples.length; i++) {
      var s = Math.max(-1, Math.min(1, samples[i]));
      v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  function finish() {
    var elapsed = (Date.now() - started) / 1000;
    stopAll();
    var total = chunks.reduce(function (n, c) { return n + c.length; }, 0), all = new Float32Array(total), o = 0;
    chunks.forEach(function (c) { all.set(c, o); o += c.length; });
    var blob = wav(downsample(all, inRate, RATE));
    preview.src = URL.createObjectURL(blob); preview.hidden = false;
    var dt = new DataTransfer();
    dt.items.add(new File([blob], "voice-note.wav", { type: "audio/wav" }));
    fileInput.files = dt.files;
    secondsInput.value = Math.min(MAX, Math.max(1, Math.round(elapsed)));
    form.hidden = false; help.hidden = true;
  }

  startBtn.addEventListener("click", function () {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert("This browser can't record audio. Try Chrome, Edge or Safari over https.");
      return;
    }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
      stream = s; chunks = [];
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      inRate = ctx.sampleRate;
      source = ctx.createMediaStreamSource(stream);
      processor = ctx.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = function (e) { chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
      source.connect(processor); processor.connect(ctx.destination);
      started = Date.now(); form.hidden = true; preview.hidden = true;
      startBtn.hidden = true; stopBtn.hidden = false;
      timer = setInterval(function () {
        var s = Math.floor((Date.now() - started) / 1000);
        time.textContent = "● 0:" + String(s).padStart(2, "0") + " / 0:30";
        if (s >= MAX) finish();
      }, 200);
    }).catch(function () { alert("Allow microphone access to record a voice note."); });
  });
  stopBtn.addEventListener("click", finish);
  document.getElementById("rec-discard").addEventListener("click", function () {
    form.hidden = true; preview.hidden = true; help.hidden = false; time.textContent = "";
  });
})();
