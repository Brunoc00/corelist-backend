from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'first_name',
            'last_name',
        )


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        validators=[validate_password]
    )

    class Meta:
        model = User
        fields = (
            'email',
            'password',
            'first_name',
            'last_name',
        )

    def create(self, validated_data):
        return User.objects.create_user(
            **validated_data
        )