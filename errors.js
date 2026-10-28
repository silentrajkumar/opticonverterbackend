export class ConversionError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ConversionError";
    this.status = status;
  }
}
