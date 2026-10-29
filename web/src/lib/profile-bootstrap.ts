declare global {
  interface Window {
    __HERMES_DASHBOARD_PROFILE__?: string;
    __HERMES_INITIAL_PROFILE__?: string;
  }
}

/**
 * The unified-launch preselect (`--open-profile`) is a FIRST-LOAD scope: it
 * seeds the management scope on the page load that boots the dashboard, and
 * must not win again on a later reload in the same tab. "default" is spelled
 * as the ABSENCE of `?profile=`, so without this once-only mark it is the one
 * scope a reload cannot express — the server-injected preselect would win
 * `initialProfileScope` again and yank the page back to the launch profile
 * after every model-switch confirm or manual refresh (#146342).
 *
 * sessionStorage is per-tab, so a fresh tab (or a deep link opened into one)
 * still gets the preselect. The consumption runs once at module load, so
 * every caller in a page load sees the same answer. When sessionStorage is
 * unavailable (privacy mode, storage disabled) the preselect degrades to the
 * old apply-on-every-load behavior rather than being dropped entirely.
 */
const BOOTSTRAP_PROFILE_CONSUMED_KEY = "hermes.bootstrap-profile.consumed";

function consumeDashboardInitialProfile(): string {
  if (typeof window === "undefined") return "";
  const initial = window.__HERMES_INITIAL_PROFILE__;
  if (!initial) return "";
  try {
    if (sessionStorage.getItem(BOOTSTRAP_PROFILE_CONSUMED_KEY)) return "";
    sessionStorage.setItem(BOOTSTRAP_PROFILE_CONSUMED_KEY, "1");
  } catch {
    return initial;
  }
  return initial;
}

const dashboardInitialProfileOnce = consumeDashboardInitialProfile();

export function dashboardInitialProfile(): string {
  return dashboardInitialProfileOnce;
}

/**
 * The profile this backend process itself serves, injected by the server and
 * empty when it cannot be named unambiguously (custom HERMES_HOME).
 *
 * It is the LAST fallback for the management scope: without it the dashboard
 * sends no `?profile=` at all, which a multi-profile host now refuses (400) on
 * every destructive route. It is never a guess — the backend only emits a name
 * that provably resolves back to its own home, so it targets exactly the home
 * an unnamed request used to reach.
 */
export function dashboardServingProfile(): string {
  if (typeof window === "undefined") return "";
  return window.__HERMES_DASHBOARD_PROFILE__ ?? "";
}

export function initialProfileScope(
  searchParams: URLSearchParams,
  bootstrapProfile = dashboardInitialProfile(),
  servingProfile = dashboardServingProfile(),
): string {
  const urlProfile = searchParams.get("profile");
  if (urlProfile !== null) return urlProfile;
  return bootstrapProfile || servingProfile;
}

export function shouldAdoptActiveProfile(
  urlProfile: string | null,
  bootstrapProfile: string,
  currentProfile: string,
  activeProfile: string,
): boolean {
  // Only the machine (default-hosted) dashboard aligns to the sticky active
  // profile. A dashboard hosted by a named profile (`hermes -p demo serve`)
  // serves THAT agent: adopting the machine-global active_profile would
  // silently retarget its banner, chat TUI env and model to another profile.
  return (
    urlProfile === null &&
    !bootstrapProfile &&
    currentProfile === "default" &&
    activeProfile !== currentProfile
  );
}
