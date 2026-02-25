// Copyright 2025 IBM Corp.
// Licensed under the Apache License, Version 2.0

/**
 * Validate environment variable name according to Kubernetes rules.
 *
 * Must start with a letter or underscore, followed by any combination
 * of letters, digits, or underscores.
 */
export const isValidEnvVarName = (name: string): boolean => {
  if (!name) return false;
  const pattern = /^[A-Za-z_][A-Za-z0-9_]*$/;
  return pattern.test(name);
};
