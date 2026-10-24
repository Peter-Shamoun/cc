"""AWS-shaped errors. `.response` matches botocore's ClientError, so handlers written for boto3
(`e.response["Error"]["Code"] == "AccessDenied"`) work unchanged against the in-process clients."""
from __future__ import annotations


class AWSError(Exception):
    code = "InternalError"
    status = 500

    def __init__(self, message: str = "", code: str | None = None, status: int | None = None, **extra):
        self.code = code or self.code
        self.status = status or self.status
        self.message = message or self.code
        self.extra = extra
        super().__init__(f"{self.code}: {self.message}")

    @property
    def response(self) -> dict:
        return {"Error": {"Code": self.code, "Message": self.message, **self.extra},
                "ResponseMetadata": {"HTTPStatusCode": self.status}}


class AccessDenied(AWSError):
    code, status = "AccessDenied", 403


class ExpiredToken(AWSError):
    code, status = "ExpiredToken", 400


class InvalidAccessKeyId(AWSError):
    code, status = "InvalidAccessKeyId", 403


class SignatureDoesNotMatch(AWSError):
    code, status = "SignatureDoesNotMatch", 403


class NoSuchKey(AWSError):
    code, status = "NoSuchKey", 404


class NoSuchBucket(AWSError):
    code, status = "NoSuchBucket", 404


class NoSuchVersion(AWSError):
    code, status = "NoSuchVersion", 404


class PreconditionFailed(AWSError):
    code, status = "PreconditionFailed", 412


class NotModified(AWSError):
    code, status = "NotModified", 304


class InvalidRange(AWSError):
    code, status = "InvalidRange", 416


class InvalidRequest(AWSError):
    code, status = "InvalidRequest", 400


class InvalidArgument(AWSError):
    code, status = "InvalidArgument", 400


class KMSDisabled(AWSError):
    code, status = "KMS.DisabledException", 400


class KMSInvalidState(AWSError):
    code, status = "KMS.KMSInvalidStateException", 400


class KMSInvalidCiphertext(AWSError):
    code, status = "KMS.InvalidCiphertextException", 400


class NotImplementedOp(AWSError):
    code, status = "NotImplemented", 501


class InvalidIdentityToken(AWSError):
    code, status = "InvalidIdentityToken", 400


class ValidationError(AWSError):
    code, status = "ValidationError", 400


class AuthError(AWSError):
    """Entra ID / Cognito sign-in refusal (not an AWS API error in prod, but shaped the same way)."""
    code, status = "NotAuthorizedException", 400
