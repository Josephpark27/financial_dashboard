import { NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  let input: { ticker?: string; quarter?: string; cached_only?: boolean };
  try {
    input = await request.json();
  } catch {
    return NextResponse.json({ detail: "A ticker and fiscal quarter are required." }, { status: 400 });
  }

  const ticker = typeof input.ticker === "string" ? input.ticker : "";
  const quarter = typeof input.quarter === "string" ? input.quarter : "";
  const backend = process.env.PYTHON_API_URL || "http://127.0.0.1:8000";

  try {
    const response = await fetch(`${backend}/api/earnings-transcript`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker, quarter, cached_only: input.cached_only === true }),
      cache: "no-store",
    });
    const body = await response.json();
    return NextResponse.json(body, { status: response.status });
  } catch {
    return NextResponse.json({ detail: "Python API is not running. Start FastAPI on 127.0.0.1:8000." }, { status: 503 });
  }
}
