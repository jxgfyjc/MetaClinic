// Client-side auth helpers (updatev2_2 §1c)

export type DoctorInfo = {
  id: string;
  name: string;
  role: string;
};

/** Fetch current logged-in doctor from the backend (cookie-based). */
export async function getDoctorInfo(): Promise<DoctorInfo | null> {
  try {
    const r = await fetch(
      `${process.env.NEXT_PUBLIC_API_BASE || ""}/api/auth/me`,
      { credentials: "include" }
    );
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

/** Logout: clear cookie server-side, then redirect to login. */
export async function logout(): Promise<void> {
  try {
    await fetch(
      `${process.env.NEXT_PUBLIC_API_BASE || ""}/api/auth/logout`,
      { method: "POST", credentials: "include" }
    );
  } catch {
    // ignore — redirect anyway
  }
  window.location.href = "/login";
}
