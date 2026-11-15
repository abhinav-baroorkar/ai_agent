// Classic script (not a module) so it still runs when the page is opened
// straight from disk, where browsers refuse to load modules.
if (location.protocol === 'file:') document.getElementById('file-warning').hidden = false;
