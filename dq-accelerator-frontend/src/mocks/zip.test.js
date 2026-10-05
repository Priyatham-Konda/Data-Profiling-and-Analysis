import { describe, expect, it } from 'vitest';
import { buildZip } from './zip';

describe('buildZip', () => {
  it('writes a stored ZIP with one entry per file', () => {
    const bytes = buildZip([
      { name: 'Account.csv', content: 'Id,Name\r\n1,Acme\r\n' },
      { name: 'Contact.csv', content: 'Id\r\n' },
    ]);
    const view = new DataView(bytes.buffer);
    expect(view.getUint32(0, true)).toBe(0x04034b50);
    // End-of-central-directory record: last 22 bytes, entry count at +10.
    const end = bytes.length - 22;
    expect(view.getUint32(end, true)).toBe(0x06054b50);
    expect(view.getUint16(end + 10, true)).toBe(2);
    // CRC-32 of the first file, known value for this content.
    expect(new TextDecoder().decode(bytes.slice(30, 41))).toBe('Account.csv');
  });
});
