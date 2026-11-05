/*
 * Copyright Red Hat
 */

export function errorMessageFromUnknown(err: unknown): string {
  if (err instanceof Error) {
    return err.message;
  }
  if (typeof err === "string") {
    return err;
  }
  if (
    typeof err === "object" &&
    err !== null &&
    "message" in err &&
    typeof (err as { message: unknown }).message === "string"
  ) {
    return (err as { message: string }).message;
  }
  try {
    const serialized = JSON.stringify(err);
    return typeof serialized === "string" ? serialized : "Unknown error";
  } catch {
    return "Unknown error";
  }
}

export function toError(err: unknown): Error {
  return err instanceof Error ? err : new Error(errorMessageFromUnknown(err));
}
