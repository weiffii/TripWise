// Только фиксированные локальные ресурсы, без URL из пользовательского ввода.
window.destinationImagePath = city => ({'Москва':'/assets/moscow-sunset.jpg','Париж':'/assets/paris-sunset.jpg','Стамбул':'/assets/istanbul-panorama.jpg'}[city]||'/assets/travel-landscape.svg');
window.setDestinationVisual = city => document.documentElement.style.setProperty('--destination-image',`url('${window.destinationImagePath(city)}')`);
