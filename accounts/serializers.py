from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class AdminTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["type"] = "admin"
        token["company_id"] = str(user.company_id)
        token["role"] = user.role
        return token


class ApiClientTokenRequestSerializer(serializers.Serializer):
    client_id = serializers.UUIDField()
    client_secret = serializers.CharField(write_only=True, trim_whitespace=False)
