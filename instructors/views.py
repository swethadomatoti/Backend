from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser
from rest_framework import status

from django.contrib.auth import get_user_model

from .models import Instructor
from .tasks import send_instructor_credentials_email_sync

User = get_user_model()



from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser
from rest_framework import status

from .models import Instructor


from .serializers import InstructorCreateSerializer

class InstructorRegisterView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        serializer = InstructorCreateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            instructor = serializer.save()

            # Send credentials email immediately so the admin doesn't rely on a worker.
            password = getattr(instructor, "_generated_password", None)
            email_warning = None
            if password:
                try:
                    send_instructor_credentials_email_sync(
                        instructor.email,
                        instructor.full_name,
                        instructor.user.username if instructor.user else instructor.email,
                        password,
                    )
                except Exception as email_error:
                    email_warning = f"Instructor created, but credentials email failed: {str(email_error)}"

            response_payload = {
                "message": "Instructor created successfully. Credentials have been sent to their email.",
                "id": instructor.id,
                "email": instructor.email,
                "full_name": instructor.full_name
            }

            if email_warning:
                response_payload["message"] = "Instructor created successfully."
                response_payload["warning"] = email_warning

            return Response(response_payload, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response(
                {"error": "Failed to create instructor", "details": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


from .models import Instructor


from .serializers import InstructorListSerializer

class InstructorListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        course_id = request.query_params.get('course_id')
        if course_id:
            instructors = Instructor.objects.filter(assigned_courses__id=course_id).distinct()
        else:
            instructors = Instructor.objects.all().order_by("-created_at")

        serializer = InstructorListSerializer(instructors, many=True)

        return Response(serializer.data, status=200)
        





from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from courses.models import Course


class InstructorCoursesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):

        user = request.user

        # 🔐 Ensure user is instructor
        if not hasattr(user, "instructor"):
            return Response(
                {"error": "You are not an instructor"},
                status=status.HTTP_403_FORBIDDEN
            )

        instructor = user.instructor

        # 🔥 Get courses via batches assigned to the instructor
        # This ensures newly created batches show up as distinct entries.
        from courses.models import Batch
        batches = Batch.objects.filter(instructor=instructor, is_active=True).select_related('course', 'name')

        data = [
            {
                "id": batch.id,
                "title": batch.course.title,
                "description": getattr(batch.course, "description", ""),
                "batch_name": batch.name.name if batch.name else None,
                "course_id": batch.course.id,
            }
            for batch in batches
        ]

        return Response(data, status=status.HTTP_200_OK)
    


    
# ---------------- DEACTIVATE INSTRUCTOR ---------------- #

class DeactivateInstructorView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, id):

        try:
            instructor = Instructor.objects.get(id=id)
        except Instructor.DoesNotExist:
            return Response({"error": "Instructor not found"}, status=404)

        instructor.is_active = False

        if instructor.user:
            instructor.user.is_active = False
            instructor.user.save()

        instructor.save()

        return Response({"message": "Instructor deactivated"}, status=200)


class ActivateInstructorView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, id):
        try:
            instructor = Instructor.objects.get(id=id)
        except Instructor.DoesNotExist:
            return Response({"error": "Instructor not found"}, status=404)

        instructor.is_active = True

        if instructor.user:
            instructor.user.is_active = True
            instructor.user.save()

        instructor.save()

        return Response({"message": "Instructor activated"}, status=200)



from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import Instructor
from .serializers import InstructorDetailSerializer


class InstructorProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):

        user = request.user

        # 🔥 Admin → can view any instructor (optional)
        if user.is_superuser:
            instructor_id = request.query_params.get("id")

            if instructor_id:
                instructor = Instructor.objects.get(id=instructor_id)
            else:
                return Response({"error": "Provide instructor id"}, status=400)

        # 🔥 Instructor → view own profile
        elif hasattr(user, "instructor"):
            instructor = user.instructor

        else:
            return Response({"error": "Not authorized"}, status=403)

        serializer = InstructorDetailSerializer(instructor)
        return Response(serializer.data)

    def put(self, request):

        user = request.user

        # 🔥 Admin → update any instructor
        if user.is_superuser:
            instructor_id = request.data.get("id")

            if not instructor_id:
                return Response({"error": "Instructor id required"}, status=400)

            instructor = Instructor.objects.get(id=instructor_id)

        # 🔥 Instructor → update own profile
        elif hasattr(user, "instructor"):
            instructor = user.instructor

        else:
            return Response({"error": "Not authorized"}, status=403)

        serializer = InstructorDetailSerializer(
    instructor,
    data=request.data,
    partial=True,
    context={"request": request}   # ✅ IMPORTANT
)

        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Updated successfully"})

        return Response(serializer.errors, status=400)


# ── INSTRUCTOR DETAIL BY ID (Admin) ──────────────────────────────────────────
class InstructorDetailByIdView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, id):
        try:
            instructor = Instructor.objects.get(id=id)
        except Instructor.DoesNotExist:
            return Response({"error": "Instructor not found"}, status=404)
        serializer = InstructorDetailSerializer(instructor)
        return Response(serializer.data)

    def patch(self, request, id):
        try:
            instructor = Instructor.objects.get(id=id)
        except Instructor.DoesNotExist:
            return Response({"error": "Instructor not found"}, status=404)

        serializer = InstructorDetailSerializer(instructor, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Instructor updated successfully"})
        return Response(serializer.errors, status=400)