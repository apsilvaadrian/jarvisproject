/* Núcleo central do JARVIS: anéis giratórios, reator e barras reativas à voz (canvas 2D). */
(function () {
  const PALETTE = {
    idle:      [46, 230, 255],
    listening: [77, 255, 184],
    thinking:  [255, 176, 46],
    speaking:  [90, 220, 255],
    error:     [255, 77, 94],
  };
  const TAU = Math.PI * 2;

  class Core {
    constructor(canvas) {
      this.c = canvas;
      this.ctx = canvas.getContext("2d");
      this.state = "idle";
      this.color = PALETTE.idle.slice();
      this.level = 0;        // 0..1 suavizado
      this.target = 0;       // nível pedido (microfone ou fala)
      this.t = 0;
      this.spin = 1;         // velocidade dos anéis (acelera ao pensar)
      this.bars = new Float32Array(96);
      this.arcs = Array.from({ length: 9 }, () => ({ a: Math.random() * TAU, len: 0.2 + Math.random() * 0.9, w: 1 + Math.random() * 2 }));
      this.particles = Array.from({ length: 70 }, () => this._particle());
      new ResizeObserver(() => this._resize()).observe(canvas);
      this._resize();
      const loop = (ts) => { this._frame(ts); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }

    setState(s) { if (PALETTE[s]) this.state = s; }
    setLevel(v) { this.target = Math.max(0, Math.min(1, v)); }

    _particle() {
      return { r: 0.6 + Math.random() * 0.42, a: Math.random() * TAU, v: (Math.random() * 0.4 + 0.1) * (Math.random() < .5 ? -1 : 1), s: Math.random() * 1.6 + 0.4, o: Math.random() };
    }

    _resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const { width, height } = this.c.getBoundingClientRect();
      this.c.width = Math.max(1, width * dpr);
      this.c.height = Math.max(1, height * dpr);
      this.size = Math.min(this.c.width, this.c.height);
    }

    _rgba(a, mul = 1) {
      const [r, g, b] = this.color;
      return `rgba(${r | 0},${g | 0},${b | 0},${a * mul})`;
    }

    _frame(ts) {
      const dt = this.last ? Math.min((ts - this.last) / 1000, 0.05) : 0.016;
      this.last = ts;
      this.t += dt;

      // Transições suaves de cor, velocidade e nível
      const target = PALETTE[this.state];
      for (let i = 0; i < 3; i++) this.color[i] += (target[i] - this.color[i]) * 0.08;
      const spinTarget = this.state === "thinking" ? 4 : this.state === "speaking" ? 1.8 : this.state === "listening" ? 1.4 : 1;
      this.spin += (spinTarget - this.spin) * 0.05;
      const idleBreath = 0.06 + 0.04 * Math.sin(this.t * 1.6);
      const want = this.state === "idle" ? idleBreath : Math.max(this.target, 0.08);
      this.level += (want - this.level) * 0.18;

      this._draw(dt);
    }

    _draw(dt) {
      const ctx = this.ctx, W = this.c.width, H = this.c.height;
      const cx = W / 2, cy = H / 2, R = this.size / 2 * 0.94;
      const t = this.t, sp = this.spin, L = this.level;
      ctx.clearRect(0, 0, W, H);
      ctx.save();
      ctx.translate(cx, cy);
      ctx.globalCompositeOperation = "lighter";

      // Brilho de fundo
      let g = ctx.createRadialGradient(0, 0, 0, 0, 0, R);
      g.addColorStop(0, this._rgba(0.22 + L * 0.25));
      g.addColorStop(0.35, this._rgba(0.06 + L * 0.06));
      g.addColorStop(1, this._rgba(0));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(0, 0, R, 0, TAU); ctx.fill();

      // 1. Anel externo de marcações
      ctx.save();
      ctx.rotate(t * 0.03 * sp);
      for (let i = 0; i < 180; i++) {
        const big = i % 15 === 0, mid = i % 5 === 0;
        const r1 = R * (big ? 0.925 : mid ? 0.94 : 0.95), r2 = R * 0.97;
        ctx.strokeStyle = this._rgba(big ? 0.9 : mid ? 0.45 : 0.22);
        ctx.lineWidth = big ? 2.2 : 1;
        const a = (i / 180) * TAU;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
        ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
        ctx.stroke();
      }
      ctx.restore();
      this._ring(R * 0.985, 1, 0.25);

      // 2. Anel segmentado grosso (gira ao contrário)
      ctx.save();
      ctx.rotate(-t * 0.12 * sp);
      ctx.lineWidth = R * 0.028;
      for (let i = 0; i < 12; i++) {
        const a0 = (i / 12) * TAU + 0.04, a1 = ((i + 1) / 12) * TAU - 0.04;
        ctx.strokeStyle = this._rgba(i % 3 === 0 ? 0.75 : 0.28);
        ctx.beginPath(); ctx.arc(0, 0, R * 0.865, a0, a1); ctx.stroke();
      }
      ctx.restore();

      // 3. Arcos finos de "dados"
      ctx.save();
      ctx.rotate(t * 0.25 * sp);
      for (const arc of this.arcs) {
        ctx.strokeStyle = this._rgba(0.55);
        ctx.lineWidth = arc.w;
        ctx.beginPath(); ctx.arc(0, 0, R * 0.8, arc.a, arc.a + arc.len); ctx.stroke();
      }
      ctx.restore();
      this._ring(R * 0.77, 1, 0.2, [3, 6], -t * 0.4 * sp);

      // 4. Varredura (forte ao pensar)
      const sweepA = t * (this.state === "thinking" ? 3.2 : 0.6);
      g = ctx.createConicGradient ? ctx.createConicGradient(sweepA, 0, 0) : null;
      if (g) {
        g.addColorStop(0, this._rgba(this.state === "thinking" ? 0.35 : 0.1));
        g.addColorStop(0.12, this._rgba(0));
        g.addColorStop(1, this._rgba(0));
        ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(0, 0, R * 0.76, 0, TAU); ctx.arc(0, 0, R * 0.6, 0, TAU, true); ctx.fill();
      }

      // 5. Barras radiais reativas à voz
      const N = this.bars.length, r0 = R * 0.58;
      for (let i = 0; i < N; i++) {
        const k = i / N;
        const noise = 0.5 + 0.5 * Math.sin(k * TAU * 3 + t * 4) * Math.sin(k * TAU * 7 - t * 2.3);
        const v = L * (0.35 + 0.65 * noise);
        this.bars[i] += (v - this.bars[i]) * 0.3;
      }
      ctx.lineCap = "round";
      ctx.lineWidth = Math.max(1.5, R * 0.011);
      for (let i = 0; i < N; i++) {
        const a = (i / N) * TAU - Math.PI / 2;
        const len = R * (0.012 + this.bars[i] * 0.17);
        ctx.strokeStyle = this._rgba(0.35 + this.bars[i] * 0.65);
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
        ctx.lineTo(Math.cos(a) * (r0 + len), Math.sin(a) * (r0 + len));
        ctx.stroke();
      }

      // 6. Anéis intermediários
      this._ring(R * 0.555, 1.5, 0.6);
      this._ring(R * 0.5, 1, 0.35, [R * 0.02, R * 0.012], t * 0.5 * sp);

      // 7. Bobinas do reator (trapezoides)
      ctx.save();
      ctx.rotate(-t * 0.08 * sp);
      const coils = 10, rIn = R * 0.3, rOut = R * 0.43;
      for (let i = 0; i < coils; i++) {
        const a0 = (i / coils) * TAU + 0.07, a1 = ((i + 1) / coils) * TAU - 0.07;
        ctx.beginPath();
        ctx.arc(0, 0, rOut, a0, a1);
        ctx.arc(0, 0, rIn, a1 - 0.05, a0 + 0.05, true);
        ctx.closePath();
        const pulse = 0.25 + 0.2 * Math.sin(t * 3 + i) + L * 0.4;
        ctx.fillStyle = this._rgba(pulse * 0.5);
        ctx.fill();
        ctx.strokeStyle = this._rgba(0.8);
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
      ctx.restore();
      this._ring(R * 0.445, 2, 0.8);
      this._ring(R * 0.285, 2, 0.9);

      // 8. Triângulo interno (Mark II)
      ctx.save();
      ctx.rotate(t * 0.2 * sp);
      ctx.strokeStyle = this._rgba(0.5 + L * 0.5);
      ctx.lineWidth = 2;
      ctx.beginPath();
      for (let i = 0; i < 3; i++) {
        const a = (i / 3) * TAU - Math.PI / 2;
        ctx[i ? "lineTo" : "moveTo"](Math.cos(a) * R * 0.25, Math.sin(a) * R * 0.25);
      }
      ctx.closePath(); ctx.stroke();
      ctx.restore();

      // 9. Núcleo luminoso
      const coreR = R * (0.2 + L * 0.06 + 0.008 * Math.sin(t * 5));
      g = ctx.createRadialGradient(0, 0, 0, 0, 0, coreR);
      g.addColorStop(0, "rgba(255,255,255,0.95)");
      g.addColorStop(0.25, this._rgba(0.9));
      g.addColorStop(0.6, this._rgba(0.35));
      g.addColorStop(1, this._rgba(0));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(0, 0, coreR, 0, TAU); ctx.fill();

      // 10. Partículas orbitando
      for (const p of this.particles) {
        p.a += p.v * dt * sp * 0.6;
        p.o += dt * 0.5;
        const alpha = 0.25 + 0.55 * Math.abs(Math.sin(p.o));
        ctx.fillStyle = this._rgba(alpha);
        ctx.beginPath();
        ctx.arc(Math.cos(p.a) * R * p.r, Math.sin(p.a) * R * p.r, p.s * (this.size / 600), 0, TAU);
        ctx.fill();
      }

      ctx.restore();
    }

    _ring(r, w, a, dash, rot = 0) {
      const ctx = this.ctx;
      ctx.save();
      ctx.rotate(rot);
      ctx.strokeStyle = this._rgba(a);
      ctx.lineWidth = w;
      if (dash) ctx.setLineDash(dash);
      ctx.beginPath(); ctx.arc(0, 0, r, 0, TAU); ctx.stroke();
      ctx.restore();
    }
  }

  window.JarvisCore = Core;
})();
