// A square drawing pad, 256 units a side, as in Quick, Draw!.
// Draws `strokes`; when `editable`, sends each finished stroke as a `stroke` event.
const SIZE = 256;

export default {
  template: `
    <canvas
      ref="canvas"
      width="512"
      height="512"
      style="width: 100%; aspect-ratio: 1; touch-action: none; background: white; border-radius: 0.5rem"
      :style="{ cursor: editable ? 'crosshair' : 'default' }"
      @pointerdown="start"
      @pointermove="move"
      @pointerup="end"
      @pointerleave="end"
    ></canvas>
  `,
  props: {
    strokes: Array,
    editable: Boolean,
  },
  data() {
    return { current: null, pending: [] };
  },
  mounted() {
    this.draw();
  },
  watch: {
    strokes() {
      this.pending = [];
      this.draw();
    },
  },
  methods: {
    point(event) {
      const box = this.$refs.canvas.getBoundingClientRect();
      const x = ((event.clientX - box.left) / box.width) * SIZE;
      const y = ((event.clientY - box.top) / box.height) * SIZE;
      return [Math.min(Math.max(Math.round(x), 0), SIZE - 1), Math.min(Math.max(Math.round(y), 0), SIZE - 1)];
    },
    start(event) {
      if (!this.editable) return;
      this.$refs.canvas.setPointerCapture(event.pointerId);
      this.current = [this.point(event)];
      this.draw();
    },
    move(event) {
      if (!this.current) return;
      const [x, y] = this.point(event);
      const [px, py] = this.current[this.current.length - 1];
      if (x === px && y === py) return;
      this.current.push([x, y]);
      this.draw();
    },
    end() {
      if (!this.current) return;
      const stroke = this.current;
      this.current = null;
      this.pending.push(stroke);
      this.$emit("stroke", stroke);
      this.draw();
    },
    draw() {
      const canvas = this.$refs.canvas;
      const ctx = canvas.getContext("2d");
      const scale = canvas.width / SIZE;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.lineWidth = 3 * scale;
      ctx.strokeStyle = "black";
      ctx.fillStyle = "black";
      const all = [...(this.strokes || []), ...this.pending, ...(this.current ? [this.current] : [])];
      for (const stroke of all) {
        if (stroke.length === 1) {
          ctx.beginPath();
          ctx.arc(stroke[0][0] * scale, stroke[0][1] * scale, 1.5 * scale, 0, 2 * Math.PI);
          ctx.fill();
          continue;
        }
        ctx.beginPath();
        ctx.moveTo(stroke[0][0] * scale, stroke[0][1] * scale);
        for (const [x, y] of stroke.slice(1)) ctx.lineTo(x * scale, y * scale);
        ctx.stroke();
      }
    },
  },
};
