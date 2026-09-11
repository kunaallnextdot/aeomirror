/* Contact & Support page (/contact).
   Two-column layout: info + FAQ on the left, the contact form on the right.
   Aurora (light) design system — self-contained `ct-*` styles on `--au-*` tokens.
   Submits to POST /api/contact. */
import React, { useMemo, useState } from "react";
import {
  Mail, Clock, Send, CheckCircle2, AlertTriangle, LifeBuoy, MessageSquareText,
} from "lucide-react";
import { submitContact, ScanError } from "../api.js";

export const SUPPORT_EMAIL = "aeomirror.support@gmail.com";
const MSG_MIN = 20;
const MSG_MAX = 5000;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const FAQ = [
  { q: "How fast will I get a reply?", a: "Our support team responds within 24 hours, Monday to Friday." },
  { q: "How does the AI Readiness Score work?", a: "We run 10 signals (crawler access, schema, structure, freshness and more) and score each 0–100." },
  { q: "Do you offer help fixing issues?", a: "Yes — every report ships with prioritized, step-by-step fixes. Ask us anything about them here." },
];

const empty = { name: "", email: "", website: "", subject: "", message: "" };

export default function Contact() {
  const [form, setForm] = useState(empty);
  const [touched, setTouched] = useState({});
  const [sending, setSending] = useState(false);
  const [toast, setToast] = useState(null);       // { kind: "ok"|"err", text }
  const [done, setDone] = useState(false);

  const errors = useMemo(() => validate(form), [form]);
  const isValid = Object.keys(errors).length === 0;

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const blur = (k) => () => setTouched((t) => ({ ...t, [k]: true }));
  const showErr = (k) => touched[k] && errors[k];

  const onSubmit = async (e) => {
    e.preventDefault();
    setTouched({ name: true, email: true, subject: true, message: true });
    if (!isValid || sending) return;
    setSending(true);
    setToast(null);
    try {
      const payload = {
        name: form.name.trim(),
        email: form.email.trim(),
        website: form.website.trim() || undefined,
        subject: form.subject.trim(),
        message: form.message.trim(),
      };
      await submitContact(payload);
      setForm(empty);
      setTouched({});
      setDone(true);
      setToast({ kind: "ok", text: "Message sent — we'll reply within 24 hours." });
    } catch (err) {
      setToast({ kind: "err", text: err instanceof ScanError ? err.message : "Could not send your message. Please try again." });
    } finally {
      setSending(false);
      setTimeout(() => setToast(null), 6000);
    }
  };

  const msgLen = form.message.length;
  const msgColor = msgLen === 0 ? "var(--au-muted)"
    : msgLen < MSG_MIN || msgLen > MSG_MAX ? "var(--au-peach-d)" : "var(--au-ink-2)";

  return (
    <div className="ct-wrap">
      <style>{CSS}</style>

      {toast && (
        <div className={`ct-toast ${toast.kind === "ok" ? "ok" : "err"}`} role="status">
          {toast.kind === "ok" ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
          <span>{toast.text}</span>
        </div>
      )}

      <div className="ct-grid">
        {/* LEFT: info + FAQ */}
        <div className="ct-info">
          <div className="ct-eyebrow"><LifeBuoy size={13} /> CONTACT &amp; SUPPORT</div>
          <h1 className="ct-title">Talk to us</h1>
          <p className="ct-lede">
            Questions about a scan, your report, billing or anything else? Send us a
            message and a real person will get back to you.
          </p>

          <div className="ct-meta">
            <div className="ct-meta-row">
              <Mail size={16} />
              <div>
                <div className="ct-meta-k">Support email</div>
                <a className="ct-link" href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
              </div>
            </div>
            <div className="ct-meta-row">
              <Clock size={16} />
              <div>
                <div className="ct-meta-k">Response time</div>
                <div className="ct-meta-v">Within 24 hours</div>
              </div>
            </div>
          </div>

          <div className="ct-faq">
            <div className="ct-faq-h"><MessageSquareText size={14} /> Frequently asked</div>
            {FAQ.map((f) => (
              <div key={f.q} className="ct-faq-item">
                <div className="ct-faq-q">{f.q}</div>
                <div className="ct-faq-a">{f.a}</div>
              </div>
            ))}
          </div>
        </div>

        {/* RIGHT: form */}
        <div className="ct-card">
          {done ? (
            <div className="ct-success">
              <div className="ct-success-icon"><CheckCircle2 size={34} /></div>
              <h2>Thank you.</h2>
              <p>We've received your message and will reply within 24 hours.</p>
              <button className="ct-btn ghost" onClick={() => setDone(false)}>Send another message</button>
            </div>
          ) : (
            <form className="ct-form" onSubmit={onSubmit} noValidate>
              <div className="ct-field">
                <label htmlFor="ct-name">Full name <span className="req">*</span></label>
                <input id="ct-name" value={form.name} onChange={set("name")} onBlur={blur("name")}
                  className={showErr("name") ? "bad" : ""} placeholder="Jane Doe" autoComplete="name" maxLength={120} />
                {showErr("name") && <div className="ct-err">{errors.name}</div>}
              </div>

              <div className="ct-field">
                <label htmlFor="ct-email">Email <span className="req">*</span></label>
                <input id="ct-email" type="email" value={form.email} onChange={set("email")} onBlur={blur("email")}
                  className={showErr("email") ? "bad" : ""} placeholder="you@company.com" autoComplete="email" maxLength={200} />
                {showErr("email") && <div className="ct-err">{errors.email}</div>}
              </div>

              <div className="ct-field">
                <label htmlFor="ct-website">Website <span className="opt">(optional)</span></label>
                <input id="ct-website" value={form.website} onChange={set("website")}
                  placeholder="https://yoursite.com" maxLength={300} />
              </div>

              <div className="ct-field">
                <label htmlFor="ct-subject">Subject <span className="req">*</span></label>
                <input id="ct-subject" value={form.subject} onChange={set("subject")} onBlur={blur("subject")}
                  className={showErr("subject") ? "bad" : ""} placeholder="What's this about?" maxLength={200} />
                {showErr("subject") && <div className="ct-err">{errors.subject}</div>}
              </div>

              <div className="ct-field">
                <label htmlFor="ct-message">Message <span className="req">*</span></label>
                <textarea id="ct-message" value={form.message} onChange={set("message")} onBlur={blur("message")}
                  className={showErr("message") ? "bad" : ""} rows={6} maxLength={MSG_MAX}
                  placeholder="Tell us how we can help…" />
                <div className="ct-counter">
                  {showErr("message")
                    ? <span className="ct-err inline">{errors.message}</span>
                    : <span />}
                  <span className="ct-count" style={{ color: msgColor }}>{msgLen} / {MSG_MAX}</span>
                </div>
              </div>

              <button type="submit" className={`ct-btn${isValid ? "" : " dim"}`} disabled={sending}>
                {sending ? <><span className="ct-spin" /> Sending…</> : <><Send size={15} /> Send message</>}
              </button>
              <div className="ct-fineprint">We'll only use your details to reply to this request.</div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

function validate(f) {
  const e = {};
  if (!f.name.trim()) e.name = "Please enter your name.";
  if (!f.email.trim()) e.email = "Please enter your email.";
  else if (!EMAIL_RE.test(f.email.trim())) e.email = "Enter a valid email address.";
  if (!f.subject.trim()) e.subject = "Please enter a subject.";
  const m = f.message.trim();
  if (!m) e.message = "Please enter a message.";
  else if (m.length < MSG_MIN) e.message = `Message must be at least ${MSG_MIN} characters.`;
  else if (m.length > MSG_MAX) e.message = `Message must be at most ${MSG_MAX} characters.`;
  return e;
}

const CSS = `
.ct-wrap{max-width:1080px;margin:0 auto;padding:48px 24px 80px}
.ct-grid{display:grid;grid-template-columns:1fr 1.05fr;gap:40px;align-items:start}
@media (max-width:820px){.ct-grid{grid-template-columns:1fr;gap:28px}}

.ct-eyebrow{display:inline-flex;align-items:center;gap:7px;color:var(--au-primary);font-size:11px;
  letter-spacing:.12em;margin-bottom:18px;border:1px solid var(--au-line);padding:5px 11px;border-radius:var(--au-r-pill);
  font-family:var(--au-font-numeric)}
.ct-title{font-family:var(--au-font-heading);font-size:40px;line-height:1.05;font-weight:700;letter-spacing:-.02em;margin:0 0 14px;color:var(--au-ink)}
.ct-lede{color:var(--au-muted);font-size:15.5px;line-height:1.6;margin:0 0 28px;max-width:460px}

.ct-meta{display:flex;flex-direction:column;gap:14px;margin-bottom:30px}
.ct-meta-row{display:flex;gap:12px;align-items:flex-start;color:var(--au-muted)}
.ct-meta-row svg{color:var(--au-primary);margin-top:2px;flex:none}
.ct-meta-k{font-size:11.5px;color:var(--au-muted);text-transform:uppercase;letter-spacing:.06em}
.ct-meta-v{font-size:14.5px;color:var(--au-ink);font-weight:500}
.ct-link{font-size:14.5px;color:var(--au-primary);text-decoration:none;font-weight:500}
.ct-link:hover{text-decoration:underline}

.ct-faq{border-top:1px solid var(--au-line);padding-top:22px}
.ct-faq-h{display:flex;align-items:center;gap:8px;font-size:12.5px;font-weight:600;color:var(--au-muted);margin-bottom:14px}
.ct-faq-item{margin-bottom:16px}
.ct-faq-q{font-size:14px;font-weight:600;margin-bottom:4px;color:var(--au-ink)}
.ct-faq-a{font-size:13px;color:var(--au-muted);line-height:1.55}

.ct-card{border:1px solid var(--au-line);border-radius:16px;background:var(--au-solid);padding:26px;box-shadow:var(--au-sh-s)}
.ct-form{display:flex;flex-direction:column;gap:16px}
.ct-field{display:flex;flex-direction:column;gap:7px}
.ct-field label{font-size:13px;font-weight:600;color:var(--au-ink)}
.ct-field .req{color:var(--au-primary)}
.ct-field .opt{color:var(--au-muted);font-weight:400;font-size:12px}
.ct-field input,.ct-field textarea{background:var(--au-solid);border:1px solid var(--au-line);border-radius:9px;
  padding:11px 12px;color:var(--au-ink);font-size:14px;font-family:var(--au-font-body);outline:none;width:100%;resize:vertical}
.ct-field input::placeholder,.ct-field textarea::placeholder{color:var(--au-muted)}
.ct-field input:focus,.ct-field textarea:focus{border-color:var(--au-primary);outline:2px solid var(--au-primary);outline-offset:1px}
.ct-field input.bad,.ct-field textarea.bad{border-color:var(--au-peach-d)}
.ct-err{color:var(--au-peach-d);font-size:12px}
.ct-err.inline{margin:0}
.ct-counter{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:-2px}
.ct-count{font-size:11.5px;font-family:var(--au-font-numeric)}

.ct-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;background:var(--au-primary);color:#fff;
  border:none;padding:12px 18px;border-radius:var(--au-r-s);font-weight:600;font-size:14px;cursor:pointer;margin-top:4px;font-family:var(--au-font-body)}
.ct-btn:disabled{opacity:.55;cursor:default}
.ct-btn.dim{opacity:.6}
.ct-btn.ghost{background:transparent;border:1px solid var(--au-line);color:var(--au-ink-2);opacity:1}
.ct-fineprint{color:var(--au-muted);font-size:11.5px;text-align:center}

.ct-spin{width:14px;height:14px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;border-radius:50%;
  display:inline-block;animation:ctspin .7s linear infinite}
@keyframes ctspin{to{transform:rotate(360deg)}}

.ct-success{text-align:center;padding:26px 8px}
.ct-success-icon{color:var(--au-mint-d);display:flex;justify-content:center;margin-bottom:12px}
.ct-success h2{font-family:var(--au-font-heading);font-size:24px;margin:0 0 8px;color:var(--au-ink)}
.ct-success p{color:var(--au-muted);font-size:14.5px;line-height:1.6;margin:0 0 20px}

.ct-toast{position:fixed;top:18px;right:18px;z-index:80;display:flex;align-items:center;gap:9px;
  padding:12px 15px;border-radius:10px;font-size:13.5px;font-weight:500;box-shadow:0 8px 30px rgba(20,30,51,.18);
  max-width:360px}
.ct-toast.ok{background:var(--au-mint);border:1px solid var(--au-mint-d);color:var(--au-mint-d)}
.ct-toast.err{background:var(--au-peach);border:1px solid var(--au-peach-d);color:var(--au-peach-d)}
`;
