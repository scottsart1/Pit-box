/* CONNECTION's Driver profile panel.

   Shown when this copy of Your Pit Box was opened from the driver list. The
   Android app handles Switch driver itself (the pitbox://profiles link saves
   the session and closes the app). On a computer the dashboard asks the
   backend to save the session and restart on the driver list, then follows
   it there in this tab. */
(function () {
  'use strict';

  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function switchDriver(status) {
    if (!window.confirm(i18n.t('Switch driver? Your Pit Box saves the session and restarts on the driver list.'))) return;
    status.textContent = i18n.t('Saving the session…');
    try {
      const response = await fetch('/api/v1/driver-profile/switch', { method: 'POST' });
      if (!response.ok) throw new Error(String(response.status));
    } catch {
      status.textContent = i18n.t('Could not switch driver. Try again, or quit and reopen Your Pit Box.');
      return;
    }
    status.textContent = i18n.t('Restarting on the driver list…');
    // The driver list comes back on this address unless another app took it,
    // in which case Your Pit Box opens it in a new tab.
    for (let attempt = 0; attempt < 240; attempt++) {
      await wait(500);
      try {
        const health = await fetch('/api/health', { cache: 'no-store' }).then(r => (r.ok ? r.json() : null));
        if (health && health.profile_screen) { location.replace('/'); return; }
      } catch { /* Restarting. */ }
    }
    status.textContent = i18n.t('Open Your Pit Box again to choose a driver.');
  }

  async function init() {
    const panel = document.getElementById('driverProfilePanel');
    if (!panel) return;
    let profile = null;
    try {
      const response = await fetch('/api/v1/driver-profile', { cache: 'no-store' });
      if (response.ok) profile = await response.json();
    } catch { return; }
    if (!profile || !profile.active) return;
    document.getElementById('driverProfileName').textContent = profile.name;
    const restart = profile.switch === 'restart';
    document.getElementById('driverProfileCloseHelp').hidden = restart;
    document.getElementById('driverProfileRestartHelp').hidden = !restart;
    if (restart) {
      const status = document.getElementById('driverProfileStatus');
      document.getElementById('driverProfileSwitch').addEventListener('click', event => {
        event.preventDefault();
        switchDriver(status);
      });
    }
    panel.hidden = false;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
