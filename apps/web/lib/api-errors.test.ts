import { describe, expect, it } from "vitest";

import { ApiError, validationMessage } from "./api";

describe("validation errors", () => {
  it("use the server's reasons instead of the generic line", () => {
    const err = new ApiError(422, {
      error: {
        code: "validation_error",
        message: "Request validation failed",
        details: {
          errors: [
            { loc: ["body", "url"], msg: "Value error, the URL appears to carry a credential" },
          ],
        },
      },
    });
    expect(err.message).toBe("the URL appears to carry a credential");
    expect(err.code).toBe("validation_error");
  });

  it("fall back to the generic line when there are no reasons", () => {
    expect(validationMessage(undefined)).toBeNull();
    const err = new ApiError(422, {
      error: { code: "validation_error", message: "Request validation failed" },
    });
    expect(err.message).toBe("Request validation failed");
  });

  it("leave other errors alone", () => {
    const err = new ApiError(409, { error: { code: "taken", message: "Name taken" } });
    expect(err.message).toBe("Name taken");
  });
});
