(() => {
  const root = document.documentElement;

  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (e) {}
    });
  });

  document.querySelectorAll("[data-toggle-search]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector("[data-search-panel]")?.classList.toggle("hidden");
      button.classList.add("hidden");
    });
  });

  const dataNode = document.getElementById("cities-data");
  const cities = dataNode ? JSON.parse(dataNode.textContent) : [];
  const norm = (s) => s.toLowerCase().replace(/ё/g, "е").trim();

  function setupCityField(field) {
    const input = field.querySelector("[data-city-input]");
    const hidden = field.querySelector("[data-city-value]");
    const list = field.querySelector("[data-city-list]");
    let active = -1;
    let shown = [];

    const close = () => { list.classList.add("hidden"); active = -1; };
    const pick = (city) => {
      input.value = city.name;
      hidden.value = city.slug;
      input.setCustomValidity("");
      close();
    };

    const render = () => {
      const query = norm(input.value);
      shown = cities.filter((c) => !query || norm(c.name).includes(query)).slice(0, 8);
      list.innerHTML = "";
      if (!shown.length) {
        list.innerHTML = '<li class="px-3 py-2 text-sm text-muted">Такого города пока нет</li>';
      }
      shown.forEach((city, i) => {
        const li = document.createElement("li");
        li.textContent = city.name;
        li.className = "cursor-pointer rounded-xl px-3 py-2 font-semibold hover:bg-surface-muted" +
          (i === active ? " bg-brand-soft text-brand" : "");
        li.addEventListener("mousedown", (e) => { e.preventDefault(); pick(city); });
        list.appendChild(li);
      });
      list.classList.remove("hidden");
    };

    input.addEventListener("focus", () => { input.select(); render(); });
    input.addEventListener("input", () => { hidden.value = ""; active = -1; render(); });
    input.addEventListener("blur", () => {
      const exact = cities.find((c) => norm(c.name) === norm(input.value));
      if (exact) pick(exact);
      close();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!shown.length) return;
        active = (active + (e.key === "ArrowDown" ? 1 : shown.length - 1)) % shown.length;
        render();
      } else if (e.key === "Enter" && active >= 0) {
        e.preventDefault();
        pick(shown[active]);
      } else if (e.key === "Escape") {
        close();
      }
    });
  }

  document.querySelectorAll("[data-search-form]").forEach((form) => {
    form.querySelectorAll("[data-city-field]").forEach(setupCityField);

    form.querySelector("[data-swap]")?.addEventListener("click", () => {
      const [a, b] = form.querySelectorAll("[data-city-field]");
      const ai = a.querySelector("[data-city-input]"), bi = b.querySelector("[data-city-input]");
      const av = a.querySelector("[data-city-value]"), bv = b.querySelector("[data-city-value]");
      [ai.value, bi.value] = [bi.value, ai.value];
      [av.value, bv.value] = [bv.value, av.value];
    });

    form.addEventListener("submit", (e) => {
      for (const field of form.querySelectorAll("[data-city-field]")) {
        const input = field.querySelector("[data-city-input]");
        if (!field.querySelector("[data-city-value]").value) {
          e.preventDefault();
          input.setCustomValidity("Выберите город из списка");
          input.reportValidity();
          return;
        }
      }
    });
  });

  const quick = document.querySelector("[data-quick-dates-light]");
  const dateInput = document.getElementById("travel-date");
  if (quick && dateInput) {
    const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    const today = new Date();
    const add = (n) => { const d = new Date(today); d.setDate(d.getDate() + n); return d; };
    const friday = add((5 - today.getDay() + 7) % 7 || 7);
    const options = [["Сегодня", today], ["Завтра", add(1)], ["В пятницу", friday], ["Через неделю", add(7)]];
    quick.insertAdjacentHTML("beforeend", '<span class="text-muted">Быстро:</span>');
    options.forEach(([label, d]) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = label;
      chip.addEventListener("click", () => {
        dateInput.value = iso(d);
        quick.querySelectorAll(".chip").forEach((c) => c.classList.remove("border-brand", "text-brand"));
        chip.classList.add("border-brand", "text-brand");
      });
      quick.appendChild(chip);
    });
  }
})();
