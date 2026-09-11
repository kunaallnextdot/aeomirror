// Shared auth password-strength policy.
// The presentational primitives that once lived here (AuthShell / Field / TextInput /
// PasswordInput / PasswordStrength / Alert / SubmitButton / Avatar / RoleBadge) were
// replaced by the Aurora primitives in dashboard/aurora.jsx and removed in the #14
// cleanup. Only the pure policy helper remains — imported by the auth pages and by the
// Aurora AuPasswordStrength.

// 0..4 strength estimate + hint text mirroring the backend policy.
export function passwordStrength(pw) {
  if (!pw) return { score: 0, ok: false, msg: "At least 8 characters, with a letter and a number." };
  let score = 0;
  if (pw.length >= 8) score++;
  if (/[A-Za-z]/.test(pw) && /\d/.test(pw)) score++;
  if (pw.length >= 12) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  const ok = pw.length >= 8 && /[A-Za-z]/.test(pw) && /\d/.test(pw);
  const msg = ok ? "Looks good." : "At least 8 characters, with a letter and a number.";
  return { score, ok, msg };
}
