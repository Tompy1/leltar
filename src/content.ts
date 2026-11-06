// Itt szerkesztheted a videó tartalmát.
export type Scene = {title: string; text: string; color: string};

export const scenes: Scene[] = [
  {title: 'Leltár', text: 'Egyszerű készletkezelés', color: '#1d4ed8'},
  {title: 'Tételek', text: 'Minden termék egy helyen', color: '#047857'},
  {title: 'Ellenőrzés', text: 'Mindig naprakész készlet', color: '#b45309'},
  {title: 'Köszönjük!', text: 'leltar', color: '#7c3aed'},
];

export const SCENE_FRAMES = 90; // 3 mp @ 30 fps
