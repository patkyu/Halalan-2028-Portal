// Matches a typed name to a known person, forgiving typos ("Sarah Dturete" → Sara Duterte),
// word order, titles ("VP", "Sen.") and common nicknames from data.json poll.aliases.
// Shared by the page (window.NameMatch) and the poll API (require).
(function (root) {
  const TITLES = new Set(["vp", "vice", "pres", "president", "presidente", "pangulo", "sen", "senator", "senador", "sec", "secretary",
    "mayor", "gov", "governor", "cong", "congressman", "congresswoman", "rep", "atty", "attorney", "dr", "mr", "ms", "mrs", "hon", "si", "ni", "kay"]);
  function norm(s) {
    return String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
      .replace(/[^a-z\s]/g, " ").split(/\s+/).filter(t => t && !TITLES.has(t)).join(" ");
  }
  // Damerau-Levenshtein (optimal string alignment): counts swapped letters as one typo.
  function dl(a, b) {
    const m = a.length, n = b.length, d = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)]);
    for (let j = 1; j <= n; j++) d[0][j] = j;
    for (let i = 1; i <= m; i++) for (let j = 1; j <= n; j++) {
      const c = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
    }
    return d[m][n];
  }
  const sim = (a, b) => (!a || !b) ? 0 : 1 - dl(a, b) / Math.max(a.length, b.length);
  const sorted = s => s.split(" ").sort().join(" ");

  // people: [{id, name, aliases: []}]
  function build(people) {
    return people.map(p => {
      const keys = [p.name, ...(p.aliases || [])].map(norm).filter(Boolean);
      return { id: p.id, name: p.name, keys, surname: norm(p.name).split(" ").pop() };
    });
  }
  function scoreOne(q, k) {
    if (q === k) return 1;
    return Math.max(sim(q, k), sim(sorted(q), sorted(k)), sim(q.replace(/ /g, ""), k.replace(/ /g, "")));
  }
  // Returns {status: "match"|"suggest"|"none"|"empty", id?, name?, suggestions: [{id,name}]}
  function match(text, index) {
    const q = norm(text);
    if (!q) return { status: "empty", suggestions: [] };
    const single = !q.includes(" ");
    const scored = index.map(p => {
      let best = 0;
      for (const k of p.keys) {
        // A one-word entry only counts against one-word nicknames (e.g. "Leni"), never against a full name.
        if (single && k.includes(" ")) continue;
        best = Math.max(best, scoreOne(q, k));
      }
      const qt = q.split(" ");
      const surnameHit = qt.some(t => sim(t, p.surname) >= 0.8);
      return { p, best, surnameHit };
    }).sort((a, b) => b.best - a.best);
    const [top, second] = scored;
    const need = single ? 0.85 : 0.75;
    if (top && top.best >= need && (!second || top.best - second.best >= 0.1)) return { status: "match", id: top.p.id, name: top.p.name, suggestions: [] };
    const minSugg = single && q.length <= 4 ? 0.7 : 0.55; // short entries like "BBM" need a close match
    const sugg = scored.filter(s => s.best >= minSugg || (s.surnameHit && q.length > 4)).slice(0, 4).map(s => ({ id: s.p.id, name: s.p.name }));
    return { status: sugg.length ? "suggest" : "none", suggestions: sugg };
  }
  const api = { norm, match, build };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.NameMatch = api;
})(typeof window !== "undefined" ? window : globalThis);
