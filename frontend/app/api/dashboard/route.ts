import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const ticker = request.nextUrl.searchParams.get("ticker") || "AAPL";
  const years = request.nextUrl.searchParams.get("years") || "3";
  const backend = process.env.PYTHON_API_URL || "http://127.0.0.1:8000";

  try {
    const response = await fetch(
      `${backend}/api/dashboard?ticker=${encodeURIComponent(ticker)}&years=${encodeURIComponent(years)}`,
      { cache: "no-store" }
    );
    const body = await response.json();
    return NextResponse.json(body, { status: response.status });
  } catch {
    return NextResponse.json({ detail: "Python API is not running. Start FastAPI on 127.0.0.1:8000." }, { status: 503 });
  }
}
