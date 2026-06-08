/* faultline auth — Supabase email/password + magic link + GitHub OAuth.
   Works as soon as supabase-config.js has real values; before that, the
   modal opens but shows a "connect Supabase" note. */
(function () {
  "use strict";
  var URL = window.SUPABASE_URL, KEY = window.SUPABASE_ANON_KEY;
  var configured = !!(URL && KEY && URL.indexOf("YOUR_") === -1 && KEY.indexOf("YOUR_") === -1 && window.supabase);
  var sb = configured ? window.supabase.createClient(URL, KEY) : null;

  var $ = function (s) { return document.querySelector(s); };
  var overlay = $("#authOverlay");
  if (!overlay) return;
  var titleEl = $("#authTitle"), subEl = $("#authSub"), msgEl = $("#authMsg"),
      formEl = $("#authForm"), emailEl = $("#authEmail"), pwdEl = $("#authPassword"),
      submitEl = $("#authSubmit"), magicEl = $("#authMagic"),
      switchA = $("#authSwitch"), switchText = $("#authSwitchText");
  var mode = "signin";
  // Only redirect to the app on a REAL sign-in action (not on session-restore, which also fires SIGNED_IN).
  // True when arriving from an oauth/magic-link callback (hash carries the token); set true on form submit too.
  var _authAction = (location.hash || "").indexOf("access_token") >= 0;

  function msg(t, kind) { msgEl.textContent = t || ""; msgEl.className = "auth-msg" + (kind ? " " + kind : ""); }
  function setMode(m) {
    mode = m;
    if (m === "signup") {
      titleEl.textContent = "Create your account";
      subEl.textContent = "Start finding the silent failures in your agents.";
      submitEl.textContent = "Create account";
      switchText.textContent = "Already have an account?";
      switchA.textContent = "Sign in";
      pwdEl.setAttribute("autocomplete", "new-password");
    } else {
      titleEl.textContent = "Welcome back";
      subEl.textContent = "Sign in to your faultline workspace.";
      submitEl.textContent = "Sign in";
      switchText.textContent = "New to faultline?";
      switchA.textContent = "Create an account";
      pwdEl.setAttribute("autocomplete", "current-password");
    }
    msg("");
  }
  function open(m) {
    setMode(m === "signup" ? "signup" : "signin");
    overlay.classList.add("show");
    overlay.setAttribute("aria-hidden", "false");
    if (!configured) msg("Supabase isn’t connected yet — add your Project URL + anon key in supabase-config.js to enable sign-in.", "warn");
    setTimeout(function () { if (emailEl) emailEl.focus(); }, 60);
  }
  function close() { overlay.classList.remove("show"); overlay.setAttribute("aria-hidden", "true"); }

  // Open triggers (any element with data-auth="signin|signup")
  document.querySelectorAll("[data-auth]").forEach(function (el) {
    el.addEventListener("click", function (e) { e.preventDefault(); open(el.getAttribute("data-auth")); });
  });
  // deep-link: /index.html#signin or #signup opens the modal
  var _h = (location.hash || "").replace("#", "");
  if (_h === "signin" || _h === "signup") open(_h);
  var closeBtn = $("#authClose"); if (closeBtn) closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
  addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  if (switchA) switchA.addEventListener("click", function () { setMode(mode === "signin" ? "signup" : "signin"); });

  function guard() { if (!configured) { msg("Supabase not connected. Add your keys in supabase-config.js.", "warn"); return false; } return true; }

  // Email + password
  if (formEl) formEl.addEventListener("submit", async function (e) {
    e.preventDefault();
    if (!guard()) return;
    var email = (emailEl.value || "").trim(), password = pwdEl.value || "";
    if (!email) { msg("Enter your email.", "warn"); return; }
    if (password.length < 6) { msg("Password must be at least 6 characters.", "warn"); return; }
    submitEl.disabled = true; submitEl.textContent = mode === "signin" ? "Signing in…" : "Creating…";
    _authAction = true;
    try {
      var res = mode === "signin"
        ? await sb.auth.signInWithPassword({ email: email, password: password })
        : await sb.auth.signUp({ email: email, password: password });
      if (res.error) { msg(res.error.message, "err"); }
      else if (mode === "signup" && !res.data.session) { msg("Almost there — check your email to confirm your account.", "ok"); }
      else { close(); }
    } catch (err) { msg((err && err.message) || "Something went wrong.", "err"); }
    submitEl.disabled = false;
    submitEl.textContent = mode === "signin" ? "Sign in" : "Create account";
  });

  // Magic link
  if (magicEl) magicEl.addEventListener("click", async function () {
    if (!guard()) return;
    var email = (emailEl.value || "").trim();
    if (!email) { msg("Enter your email first, then request the link.", "warn"); return; }
    magicEl.disabled = true; var t = magicEl.textContent; magicEl.textContent = "Sending…";
    try {
      var r = await sb.auth.signInWithOtp({ email: email, options: { emailRedirectTo: location.origin + location.pathname } });
      if (r.error) msg(r.error.message, "err"); else msg("Magic link sent — check your inbox.", "ok");
    } catch (err) { msg((err && err.message) || "Could not send link.", "err"); }
    magicEl.disabled = false; magicEl.textContent = t;
  });

  // OAuth (GitHub)
  document.querySelectorAll(".auth-oauth").forEach(function (b) {
    b.addEventListener("click", async function () {
      if (!guard()) return;
      var provider = b.getAttribute("data-provider");
      try {
        var r = await sb.auth.signInWithOAuth({ provider: provider, options: { redirectTo: location.origin + location.pathname } });
        if (r.error) msg(r.error.message + " — enable the " + provider + " provider in Supabase → Authentication → Providers.", "err");
      } catch (err) { msg((err && err.message) || "OAuth failed.", "err"); }
    });
  });

  // Hide OAuth buttons whose provider isn't enabled in Supabase (no dead buttons).
  if (configured) {
    fetch(URL + "/auth/v1/settings", { headers: { apikey: KEY } })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        var ext = (s && s.external) || {}, anyShown = false;
        document.querySelectorAll(".auth-oauth").forEach(function (b) {
          if (ext[b.getAttribute("data-provider")]) anyShown = true; else b.style.display = "none";
        });
        if (!anyShown) { var d = document.querySelector(".auth-divider"); if (d) d.style.display = "none"; }
      }).catch(function () {});
  }

  // Logged-in nav state
  function render(user) {
    var login = document.querySelector(".nav-r .login");
    var navStart = document.querySelector(".nav-r .btn.pri");
    var navUser = $("#navUser");
    if (user) {
      if (login) login.style.display = "none";
      if (navStart) navStart.style.display = "none";
      if (navUser) {
        navUser.style.display = "flex";
        var av = $("#navUserAvatar"), em = $("#navUserEmail");
        if (av) av.textContent = ((user.email || "?")[0] || "?").toUpperCase();
        if (em) em.textContent = user.email || "account";
      }
    } else {
      if (login) login.style.display = "";
      if (navStart) navStart.style.display = "";
      if (navUser) navUser.style.display = "none";
    }
  }
  var signout = $("#navSignout");
  if (signout) signout.addEventListener("click", async function () { if (sb) await sb.auth.signOut(); });

  if (configured) {
    sb.auth.getSession().then(function (r) { render(r.data.session ? r.data.session.user : null); });
    sb.auth.onAuthStateChange(function (ev, session) {
      render(session ? session.user : null);
      if (session) { close(); if (ev === "SIGNED_IN" && _authAction) location.assign("/app/overview.html"); }
    });
  }
})();
