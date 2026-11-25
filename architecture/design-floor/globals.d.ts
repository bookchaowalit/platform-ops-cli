// Hooks the in-page probes of check.mjs leave on window between page.evaluate calls.
interface Window {
  /** Outline and box-shadow of every element before the first Tab (probeRestStyles). */
  __designFloorRest?: WeakMap<Element, { outline: string; shadow: string }>;
  /** Motion recorded since navigation (recordMotion init script). */
  __designFloorMotion?: () => Array<{ name: string; duration: unknown; iterations: unknown; el: string }>;
}
