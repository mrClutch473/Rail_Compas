(() => {
  const dataNode = document.getElementById("chart-data");
  const canvas = document.getElementById("price-chart");
  if (!dataNode || !canvas || !window.Chart) return;

  const data = JSON.parse(dataNode.textContent);
  const script = document.currentScript || document.querySelector("script[data-selected]");
  let selected = script?.dataset.selected || Object.keys(data)[0];
  let chart = null;

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const rub = (v) => Math.ceil(v).toLocaleString("ru-RU") + " ₽";
  const when = (ms) => new Date(ms).toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });

  function legend(series) {
    const list = document.querySelector("[data-legend]");
    if (!list) return;
    list.innerHTML = "";
    series.forEach((s) => {
      const li = document.createElement("li");
      li.className = "flex items-center gap-2 text-text";
      const swatch = s.reference
        ? `<span class="inline-block w-5 border-t-2 border-dashed" style="border-color: var(${s.token})"></span>`
        : `<span class="inline-block h-0.5 w-5 rounded" style="background: var(${s.token})"></span>`;
      li.innerHTML = `${swatch}<span class="font-semibold">${s.name}</span>`;
      list.appendChild(li);
    });
  }

  function render() {
    const entry = data[selected];
    if (!entry) return;
    legend(entry.series);
    const grid = css("--border");
    const ink = css("--muted");
    const surface = css("--surface");
    // carry every line to the latest collection time so the current price reads as a segment
    const lastX = Math.max(...entry.series.flatMap((s) => s.points.map(([x]) => x)));
    const datasets = entry.series.map((s) => {
      const color = css(s.token);
      const points = s.points.map(([x, y]) => ({ x, y }));
      const tail = points[points.length - 1];
      if (tail && tail.x < lastX) points.push({ x: lastX, y: tail.y });
      return {
        label: s.name,
        data: points,
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
        borderDash: s.reference ? [6, 4] : [],
        stepped: "before",
        pointRadius: points.length === 1 ? 4 : 0,
        pointHoverRadius: 5,
        pointBorderColor: surface,
        pointBorderWidth: 2,
      };
    });

    chart?.destroy();
    chart = new Chart(canvas, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: css("--surface"),
            titleColor: css("--text"),
            bodyColor: css("--text"),
            borderColor: css("--border-strong"),
            borderWidth: 1,
            padding: 10,
            usePointStyle: true,
            callbacks: {
              title: (items) => when(items[0].parsed.x),
              label: (item) => ` ${item.dataset.label}: ${rub(item.parsed.y)}`,
            },
          },
        },
        scales: {
          x: {
            type: "linear",
            grid: { display: false },
            border: { color: grid },
            ticks: { color: ink, maxTicksLimit: 6, callback: (v) => when(v) },
          },
          y: {
            grid: { color: grid },
            border: { display: false },
            ticks: { color: ink, maxTicksLimit: 6, callback: (v) => rub(v) },
          },
        },
      },
    });
  }

  document.querySelectorAll("[data-class-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      selected = tab.dataset.classTab;
      document.querySelectorAll("[data-class-tab]").forEach((t) =>
        t.setAttribute("aria-selected", String(t === tab)));
      document.querySelectorAll("[data-class-panel]").forEach((p) => {
        p.hidden = p.dataset.classPanel !== selected;
      });
      const url = new URL(location.href);
      url.searchParams.set("class", selected);
      history.replaceState(null, "", url);
      render();
    });
  });

  document.addEventListener("themechange", render);
  render();
})();
