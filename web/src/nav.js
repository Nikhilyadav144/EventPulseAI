export function go(page, arg) {
  window.location.hash = `/${page}${arg ? "/" + encodeURIComponent(arg) : ""}`;
}
