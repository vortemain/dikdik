import { expect, test } from 'bun:test';
import { ComposerSize, composerMaximum } from '../../dikdik/web/composer-size';

test('manual resize survives typing but clear and conversation switch restore automatic size', () => {
  const size = new ComposerSize();
  expect(size.fit('short', 60, 400)).toBe(60);
  size.resize(300, 400); expect(size.fit('more text', 90, 400)).toBe(300);
  expect(size.fit('', 52, 400)).toBe(52);
  size.resize(220, 400); expect(size.fit('', 52, 400)).toBe(220);
  size.reset();
  expect(size.fit('automatic growth', 190, 400)).toBe(190);
  size.resize(250, 400); size.reset(); expect(size.fit('another conversation', 80, 400)).toBe(80);
});

test('viewport shrink and expanded controls keep the resized field within available room', () => {
  const size = new ComposerSize();
  size.resize(9000, composerMaximum(900, 100));
  expect(size.fit('draft', 60, composerMaximum(900, 100))).toBe(520);
  expect(size.fit('draft', 60, composerMaximum(500, 200))).toBe(120);
  size.resize(-100, 200); expect(size.fit('draft', 300, 200)).toBe(52);
  expect(composerMaximum(200, 400)).toBe(52);
});
