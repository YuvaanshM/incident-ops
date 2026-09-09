import { describe, expect, it } from "vitest";

import { normalizeApiError } from "@/lib/api/errors";

describe("normalizeApiError", () => {
  it("preserves stable backend codes, details, field locations, and request IDs", () => {
    const error = normalizeApiError(
      {
        error: {
          code: "version_conflict",
          message: "The incident changed.",
          details: {
            current_version: 7,
            refetch: "/api/v1/incidents/example",
            fields: [
              {
                location: ["body", "title"],
                type: "value_error",
                message: "Invalid title",
              },
            ],
          },
        },
      },
      409,
      "request-123",
    );

    expect(error).toEqual({
      code: "version_conflict",
      message: "The incident changed.",
      status: 409,
      requestId: "request-123",
      details: {
        current_version: 7,
        refetch: "/api/v1/incidents/example",
        fields: [
          {
            location: ["body", "title"],
            type: "value_error",
            message: "Invalid title",
          },
        ],
      },
    });
  });

  it("returns a generic safe error for malformed upstream data", () => {
    expect(normalizeApiError("private upstream body", 500, null)).toEqual({
      code: "request_failed",
      message: "The request could not be completed.",
      status: 500,
    });
  });
});
