export function notFound(req, res) {
  res.status(404).json({ error: "Route not found" });
}

export function errorHandler(err, req, res, next) {
  if (res.headersSent) {
    return next(err);
  }

  const status = err.statusCode || err.status || 500;
  const message = status === 500 ? "Internal server error" : err.message;

  if (status === 500) {
    console.error(err);
  }

  return res.status(status).json({ error: message });
}
