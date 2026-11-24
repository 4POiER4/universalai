// Intentionally empty: the main window loads the platform's own
// /messenger page unmodified, and that page needs no bridge into Node —
// it already talks to the backend over plain HTTPS/WebSocket, exactly as
// it does in a browser tab. Keeping this empty (rather than omitting the
// preload) keeps contextIsolation/sandbox on without granting the page
// anything extra.
