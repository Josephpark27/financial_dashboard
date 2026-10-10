import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const filingUrl = request.nextUrl.searchParams.get("url");
  if (!filingUrl) return NextResponse.json({ detail: "A filing URL is required." }, { status: 400 });

  const backend = process.env.PYTHON_API_URL || "http://127.0.0.1:8000";
  try {
    const response = await fetch(
      `${backend}/api/filing-pdf?url=${encodeURIComponent(filingUrl)}`,
      { cache: "no-store" },
    );
    return NextResponse.json(await response.json(), { status: response.status });
  } catch {
    return NextResponse.json({ detail: "Python API is not running. Start FastAPI on 127.0.0.1:8000." }, { status: 503 });
  }
}
