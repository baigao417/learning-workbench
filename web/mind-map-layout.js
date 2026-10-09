(function exposeMindMapLayout(root) {
  function buildMindMapLayout(mindMap) {
    const source = Array.isArray(mindMap) ? mindMap : [];
    const rootX = 108;
    const topicX = 355;
    const childX = 650;
    const canvasWidth = 770;
    const branches = [];
    let cursorY = 30;
    for (const node of source) {
      const children = Array.isArray(node.children) ? node.children : [];
      const childCount = Math.max(1, children.length);
      const height = Math.max(118, childCount * 72);
      const center = cursorY + height / 2;
      branches.push({
        node,
        x: topicX,
        y: center,
        children: children.map((child, index) => ({
          node: child,
          x: childX,
          y: cursorY + ((index + 0.5) * height) / childCount,
        })),
      });
      cursorY += height + 24;
    }
    const canvasHeight = Math.max(430, cursorY + 10);
    return {
      canvasWidth,
      canvasHeight,
      root: { x: rootX, y: canvasHeight / 2 },
      branches,
    };
  }

  function calculateMindMapFitScale(viewportWidth, viewportHeight, canvasWidth, canvasHeight) {
    const values = [viewportWidth, viewportHeight, canvasWidth, canvasHeight].map(Number);
    if (values.some((value) => !Number.isFinite(value) || value <= 0)) return 1;
    const [viewWidth, viewHeight, contentWidth, contentHeight] = values;
    return Math.max(0.45, Math.min((viewWidth - 36) / contentWidth, (viewHeight - 36) / contentHeight, 1.25));
  }

  root.buildMindMapLayout = buildMindMapLayout;
  root.calculateMindMapFitScale = calculateMindMapFitScale;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { buildMindMapLayout, calculateMindMapFitScale };
  }
}(typeof window !== "undefined" ? window : globalThis));
