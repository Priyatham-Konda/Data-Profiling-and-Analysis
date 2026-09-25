import { describe, expect, it } from 'vitest';
import { validateCsv } from './validateCsv';
import { ACCEPTED_UPLOAD_EXTENSIONS, MAX_UPLOAD_BYTES } from '@/api/constants';

function fakeFile(name, size) {
  return { name, size };
}

describe('validateCsv', () => {
  it('accepts a normal csv', () => {
    expect(validateCsv(fakeFile('customers.csv', 2048))).toBeNull();
  });

  it.each(ACCEPTED_UPLOAD_EXTENSIONS)('accepts the backend-accepted extension %s', (ext) => {
    expect(validateCsv(fakeFile(`data${ext}`, 2048))).toBeNull();
  });

  it('accepts a file at exactly the size limit', () => {
    expect(validateCsv(fakeFile('big.csv', MAX_UPLOAD_BYTES))).toBeNull();
  });

  it('rejects one byte over the limit as an early-out, not a duplicate of the backend check', () => {
    expect(validateCsv(fakeFile('big.csv', MAX_UPLOAD_BYTES + 1))).toMatch(/limit/i);
  });

  it('rejects an extension the backend has never accepted', () => {
    expect(validateCsv(fakeFile('report.xlsx', 100))).toMatch(/isn.t a CSV/i);
  });

  it('is case insensitive about the extension', () => {
    expect(validateCsv(fakeFile('DATA.CSV', 100))).toBeNull();
    expect(validateCsv(fakeFile('DATA.TSV', 100))).toBeNull();
  });

  // Deliberately NOT re-validated here: empty files, headerless files, row
  // counts. Those are the backend's call per API_CONTRACT.md -- this file
  // only does the two things worth an instant client-side answer.
  it('rejects no file at all', () => {
    expect(validateCsv(null)).toBeTruthy();
  });
});
