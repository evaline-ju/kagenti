// Copyright 2025 IBM Corp.
// Licensed under the Apache License, Version 2.0

import { describe, it, expect } from 'vitest';
import { isValidEnvVarName } from './validation';

describe('isValidEnvVarName', () => {
  it('accepts names starting with a letter', () => {
    expect(isValidEnvVarName('MY_VAR')).toBe(true);
    expect(isValidEnvVarName('a')).toBe(true);
    expect(isValidEnvVarName('Z')).toBe(true);
  });

  it('accepts names starting with an underscore', () => {
    expect(isValidEnvVarName('_MY_VAR')).toBe(true);
    expect(isValidEnvVarName('_')).toBe(true);
    expect(isValidEnvVarName('__')).toBe(true);
  });

  it('accepts names with letters, digits, and underscores', () => {
    expect(isValidEnvVarName('VAR_123')).toBe(true);
    expect(isValidEnvVarName('a1b2c3')).toBe(true);
    expect(isValidEnvVarName('_0')).toBe(true);
  });

  it('rejects empty string', () => {
    expect(isValidEnvVarName('')).toBe(false);
  });

  it('rejects names starting with a digit', () => {
    expect(isValidEnvVarName('1VAR')).toBe(false);
    expect(isValidEnvVarName('0_FOO')).toBe(false);
  });

  it('rejects names containing invalid characters', () => {
    expect(isValidEnvVarName('MY-VAR')).toBe(false);
    expect(isValidEnvVarName('MY.VAR')).toBe(false);
    expect(isValidEnvVarName('MY VAR')).toBe(false);
    expect(isValidEnvVarName('MY@VAR')).toBe(false);
    expect(isValidEnvVarName('path/to')).toBe(false);
  });

  it('rejects names with leading or trailing spaces', () => {
    expect(isValidEnvVarName(' MY_VAR')).toBe(false);
    expect(isValidEnvVarName('MY_VAR ')).toBe(false);
  });
});
