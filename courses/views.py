from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.utils import timezone
from django.db.models import Q

from accounts.permissions import IsStudent, IsInstructor
from .models import Course, CourseContent, Category, Module, Lesson, Assignment, Submission, Batch
from .serializers import (
    CourseSerializer,
    CourseContentSerializer,
    CategorySerializer,
    ModuleSerializer,
    ModuleWriteSerializer,
    LessonSerializer,
    AssignmentSerializer,
    CourseContentDisplaySerializer,
    SubmissionSerializer,
    BatchSerializer,
)
from .permissions import IsSuperAdmin, IsAssignedInstructorOrAdmin, CanEditCourseContent, IsModuleCreator, IsAdminOrInstructor


# ════════════════════════════════════════════════════════════
# CATEGORY
# ════════════════════════════════════════════════════════════

class CategoryListCreateView(APIView):

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsSuperAdmin()]
        return [AllowAny()]

    def get(self, request):
        categories = Category.objects.all()
        serializer = CategorySerializer(categories, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CategorySerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)


class CategoryDetailView(APIView):

    def get_permissions(self):
        if self.request.method in ["PUT", "PATCH", "DELETE"]:
            return [IsSuperAdmin()]
        return [AllowAny()]

    def get(self, request, pk):
        try:
            category = Category.objects.get(pk=pk)
        except Category.DoesNotExist:
            return Response({"error": "Category not found"}, status=404)
        serializer = CategorySerializer(category)
        return Response(serializer.data)

    def put(self, request, pk):
        try:
            category = Category.objects.get(pk=pk)
        except Category.DoesNotExist:
            return Response({"error": "Category not found"}, status=404)
        serializer = CategorySerializer(category, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def patch(self, request, pk):
        return self.put(request, pk)

    def delete(self, request, pk):
        try:
            category = Category.objects.get(pk=pk)
        except Category.DoesNotExist:
            return Response({"error": "Category not found"}, status=404)
        category.delete()
        return Response({"message": "Category deleted"}, status=204)


# ════════════════════════════════════════════════════════════
# COURSE
# ════════════════════════════════════════════════════════════

class CourseListCreateView(APIView):

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsSuperAdmin()]
        return [AllowAny()]

    def get(self, request, category_id=None):
        courses = Course.objects.filter(is_active=True)
        if category_id is not None:
            courses = courses.filter(category_id=category_id)

        if request.user.is_authenticated:
            # 🔥 NEW: Allow filtering by instructor_id (for Admins)
            instructor_id = request.query_params.get('instructor_id')
            
            if request.user.role == 'student':
                from enrollments.models import Enrollment
                enrolled_ids = Enrollment.objects.filter(
                    email=request.user.email, status='approved'
                ).values_list('course_id', flat=True)
                courses = courses.filter(id__in=enrolled_ids)
            elif (request.user.is_superuser or request.user.role == 'admin') and instructor_id:
                # Filter courses assigned to a specific instructor
                from instructors.models import Instructor
                try:
                    instructor = Instructor.objects.get(id=instructor_id)
                    assigned_ids = instructor.assigned_courses.values_list('id', flat=True)
                    courses = courses.filter(id__in=assigned_ids)
                except Instructor.DoesNotExist:
                    courses = courses.none()
            elif request.user.role == 'instructor':
                if hasattr(request.user, 'instructor'):
                    assigned_ids = request.user.instructor.assigned_courses.values_list('id', flat=True)
                    courses = courses.filter(id__in=assigned_ids)
                else:
                    courses = courses.none()

        serializer = CourseSerializer(courses, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CourseSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CourseDetailView(APIView):

    def get_permissions(self):
        if self.request.method in ["PUT", "PATCH", "DELETE"]:
            return [IsAssignedInstructorOrAdmin()]
        return [AllowAny()]

    def get(self, request, pk):
        try:
            course = Course.objects.get(pk=pk, is_active=True)
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=404)

        if request.user.is_authenticated:
            if request.user.role == 'student':
                from enrollments.models import Enrollment
                if not Enrollment.objects.filter(
                    email=request.user.email, course=course, status='approved'
                ).exists():
                    return Response({"error": "You are not enrolled in this course"}, status=403)
            elif request.user.role == 'instructor':
                is_assigned = hasattr(request.user, 'instructor') and request.user.instructor.assigned_courses.filter(id=course.id).exists()
                if not is_assigned and not request.user.is_superuser:
                    return Response({"error": "You are not assigned to this course"}, status=403)

        serializer = CourseSerializer(course)
        return Response(serializer.data)

    def put(self, request, pk):
        try:
            course = Course.objects.get(pk=pk)
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=404)

        permission = IsAssignedInstructorOrAdmin()
        if not permission.has_object_permission(request, self, course):
            return Response({"error": "You don't have permission to edit this course"}, status=403)

        data = request.data.copy()
        # instructors cannot edit price
        if request.user.role == 'instructor' and not request.user.is_superuser:
            if 'price' in data:
                data.pop('price')

        serializer = CourseSerializer(course, data=data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def patch(self, request, pk):
        return self.put(request, pk)

    def delete(self, request, pk):
        try:
            course = Course.objects.get(pk=pk)
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=404)

        permission = IsAssignedInstructorOrAdmin()
        if not permission.has_object_permission(request, self, course):
            return Response({"error": "You don't have permission to delete this course"}, status=403)

        course.delete()
        return Response({"message": "Course deleted"}, status=204)


# ════════════════════════════════════════════════════════════
# COURSE CONTENT  (full structured view — Training + Industry Readiness Modules)
# GET /courses/<id>/content/
# ════════════════════════════════════════════════════════════

class CourseContentView(APIView):
    """
    Returns the full structured content of a course
    """
    permission_classes = [AllowAny]

    def get(self, request, course_id):
        try:
            course = Course.objects.get(pk=course_id, is_active=True)
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=404)

        # Role-based access check
        if request.user.is_authenticated:
            if request.user.role == 'student':
                from enrollments.models import Enrollment
                if not Enrollment.objects.filter(
                    email=request.user.email, course=course, status='approved'
                ).exists():
                    return Response({"error": "You are not enrolled in this course"}, status=403)
            elif request.user.role == 'instructor':
                is_assigned = hasattr(request.user, 'instructor') and request.user.instructor.assigned_courses.filter(id=course.id).exists()
                if not is_assigned and not request.user.is_superuser:
                    return Response({"error": "You are not assigned to this course"}, status=403)

        serializer = CourseContentDisplaySerializer(course, context={"request": request})
        return Response(serializer.data)


# ════════════════════════════════════════════════════════════
# OLD COURSE CONTENT MODEL (legacy)
# ════════════════════════════════════════════════════════════

class CourseContentListCreateView(APIView):

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAssignedInstructorOrAdmin()]
        return [AllowAny()]

    def get(self, request):
        if request.user.is_authenticated:
            if request.user.role == 'student':
                from enrollments.models import Enrollment
                enrolled_ids = Enrollment.objects.filter(
                    email=request.user.email, status='approved'
                ).values_list('course_id', flat=True)
                contents = CourseContent.objects.filter(course_id__in=enrolled_ids)
            elif request.user.role == 'instructor':
                if hasattr(request.user, 'instructor'):
                    assigned_ids = request.user.instructor.assigned_courses.values_list('id', flat=True)
                    contents = CourseContent.objects.filter(course_id__in=assigned_ids)
                else:
                    contents = CourseContent.objects.none()
            else:
                contents = CourseContent.objects.all()
        else:
            contents = CourseContent.objects.none()

        serializer = CourseContentSerializer(contents, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CourseContentSerializer(data=request.data)
        if serializer.is_valid():
            course = serializer.validated_data['course']
            permission = IsAssignedInstructorOrAdmin()
            if not permission.has_object_permission(request, self, course):
                return Response({"error": "You don't have permission to add content to this course"}, status=403)
            serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)


class CourseContentDetailView(APIView):

    def get_permissions(self):
        if self.request.method in ["PUT", "DELETE"]:
            return [CanEditCourseContent()]
        return [AllowAny()]

    def get(self, request, pk):
        try:
            content = CourseContent.objects.get(pk=pk)
        except CourseContent.DoesNotExist:
            return Response({"error": "Content not found"}, status=404)

        if request.user.is_authenticated:
            if request.user.role == 'student':
                from enrollments.models import Enrollment
                if not Enrollment.objects.filter(
                    email=request.user.email, course=content.course, status='approved'
                ).exists():
                    return Response({"error": "You are not enrolled in this course"}, status=403)
            elif request.user.role == 'instructor':
                is_assigned = hasattr(request.user, 'instructor') and request.user.instructor.assigned_courses.filter(id=content.course.id).exists()
                if not is_assigned and not request.user.is_superuser:
                    return Response({"error": "You are not assigned to this course"}, status=403)

        serializer = CourseContentSerializer(content)
        return Response(serializer.data)

    def put(self, request, pk):
        try:
            content = CourseContent.objects.get(pk=pk)
        except CourseContent.DoesNotExist:
            return Response({"error": "Content not found"}, status=404)

        permission = CanEditCourseContent()
        if not permission.has_object_permission(request, self, content):
            return Response({"error": "You don't have permission to edit this content"}, status=403)

        serializer = CourseContentSerializer(content, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def patch(self, request, pk):
        return self.put(request, pk)

    def delete(self, request, pk):
        try:
            content = CourseContent.objects.get(pk=pk)
        except CourseContent.DoesNotExist:
            return Response({"error": "Content not found"}, status=404)

        permission = CanEditCourseContent()
        if not permission.has_object_permission(request, self, content):
            return Response({"error": "You don't have permission to delete this content"}, status=403)

        content.delete()
        return Response({"message": "Content deleted"}, status=204)


# ════════════════════════════════════════════════════════════
# MODULE
# GET  /courses/<course_id>/modules/   → list modules
# POST /courses/<course_id>/modules/   → create module
# ════════════════════════════════════════════════════════════

class ModuleListCreateView(APIView):

    def get_permissions(self):
        if self.request.method == "POST":
            return [CanEditCourseContent()]
        return [AllowAny()]

    def get(self, request, course_id):
        try:
            course = Course.objects.get(id=course_id)
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=404)

        section_type = request.query_params.get("section_type")
        modules = Module.objects.filter(course=course).order_by('order')

        # Instructors should only see modules they created.
        # Admins keep full visibility.
        if request.user.is_authenticated and not request.user.is_superuser:
            if hasattr(request.user, 'instructor'):
                modules = modules.filter(created_by=request.user.instructor)
            else:
                modules = modules.none()

        if section_type:
            modules = modules.filter(section_type=section_type)
        
        serializer = ModuleSerializer(modules, many=True)
        return Response(serializer.data)

    def post(self, request, course_id):
        try:
            course = Course.objects.get(id=course_id)
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=404)

        # We pass course inside data as object permission checks against obj
        data = request.data.copy()
        data["course"] = course_id

        # To use CanEditCourseContent for course creation, we need mock obj or manual check
        permission = IsAssignedInstructorOrAdmin()
        if not permission.has_object_permission(request, self, course):
            return Response({"error": "You don't have permission to create modules for this course"}, status=403)

        serializer = ModuleWriteSerializer(data=data)
        if serializer.is_valid():
            if request.user.is_authenticated and hasattr(request.user, 'instructor'):
                module = serializer.save(created_by=request.user.instructor)
            else:
                module = serializer.save()
            return Response(ModuleSerializer(module).data, status=201)
        return Response(serializer.errors, status=400)


class ModuleDetailView(APIView):
    """
    GET    /courses/<course_id>/modules/<pk>/  → retrieve single module
    PUT    /courses/<course_id>/modules/<pk>/  → update module (instructor/admin)
    DELETE /courses/<course_id>/modules/<pk>/  → delete module (instructor/admin)
    """
    def get_permissions(self):
        if self.request.method in ["PUT", "PATCH", "DELETE"]:
            return [IsModuleCreator()]
        return [AllowAny()]

    def get(self, request, course_id, pk):
        try:
            module = Module.objects.get(pk=pk, course_id=course_id)
        except Module.DoesNotExist:
            return Response({"error": "Module not found in this course"}, status=404)
        serializer = ModuleSerializer(module)
        return Response(serializer.data)

    def put(self, request, course_id, pk):
        try:
            module = Module.objects.get(pk=pk, course_id=course_id)
        except Module.DoesNotExist:
            return Response({"error": "Module not found in this course"}, status=404)

        permission = IsModuleCreator()
        if not permission.has_object_permission(request, self, module):
            return Response({"error": "You can only edit modules you created"}, status=403)

        serializer = ModuleWriteSerializer(module, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def patch(self, request, course_id, pk):
        return self.put(request, course_id, pk)

    def delete(self, request, course_id, pk):
        try:
            module = Module.objects.get(pk=pk, course_id=course_id)
        except Module.DoesNotExist:
            return Response({"error": "Module not found in this course"}, status=404)

        permission = IsModuleCreator()
        if not permission.has_object_permission(request, self, module):
            return Response({"error": "You can only delete modules you created"}, status=403)

        module.delete()
        return Response({"message": "Module deleted"}, status=204)


# ════════════════════════════════════════════════════════════
# LESSON
# GET  /modules/<module_id>/lessons/   → list lessons
# POST /modules/<module_id>/lessons/   → create lesson
# ════════════════════════════════════════════════════════════

class LessonListCreateView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        if self.request.method == "POST":
            return [CanEditCourseContent()]
        return [AllowAny()]

    def get(self, request, module_id):
        try:
            module = Module.objects.get(id=module_id)
        except Module.DoesNotExist:
            return Response({"error": "Module not found"}, status=404)

        lessons = Lesson.objects.filter(module=module).order_by('order')
        serializer = LessonSerializer(lessons, many=True)
        return Response(serializer.data)

    def post(self, request, module_id):
        try:
            module = Module.objects.get(id=module_id)
        except Module.DoesNotExist:
            return Response({"error": "Module not found"}, status=404)

        permission = CanEditCourseContent()
        if not permission.has_object_permission(request, self, module):
            return Response({"error": "You don't have permission to create lessons for this module"}, status=403)

        data = request.data.copy()
        data["module"] = module_id

        serializer = LessonSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)


class LessonDetailView(APIView):
    """
    GET    /modules/<module_id>/lessons/<pk>/  → retrieve single lesson
    PUT    /modules/<module_id>/lessons/<pk>/  → update lesson (instructor/admin)
    DELETE /modules/<module_id>/lessons/<pk>/  → delete lesson (instructor/admin)
    """
    def get_permissions(self):
        if self.request.method in ["PUT", "PATCH", "DELETE"]:
            return [CanEditCourseContent()]
        return [AllowAny()]

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, module_id, pk):
        try:
            lesson = Lesson.objects.get(pk=pk, module_id=module_id)
        except Lesson.DoesNotExist:
            return Response({"error": "Lesson not found in this module"}, status=404)
        serializer = LessonSerializer(lesson)
        return Response(serializer.data)

    def put(self, request, module_id, pk):
        try:
            lesson = Lesson.objects.get(pk=pk, module_id=module_id)
        except Lesson.DoesNotExist:
            return Response({"error": "Lesson not found in this module"}, status=404)

        permission = CanEditCourseContent()
        if not permission.has_object_permission(request, self, lesson):
            return Response({"error": "You don't have permission to edit this lesson"}, status=403)

        serializer = LessonSerializer(lesson, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def patch(self, request, module_id, pk):
        return self.put(request, module_id, pk)

    def delete(self, request, module_id, pk):
        try:
            lesson = Lesson.objects.get(pk=pk, module_id=module_id)
        except Lesson.DoesNotExist:
            return Response({"error": "Lesson not found in this module"}, status=404)

        permission = CanEditCourseContent()
        if not permission.has_object_permission(request, self, lesson):
            return Response({"error": "You don't have permission to delete this lesson"}, status=403)

        lesson.delete()
        return Response({"message": "Lesson deleted"}, status=204)


class CourseCurriculumView(APIView):
    """Legacy endpoint — kept for backward compatibility."""
    permission_classes = [AllowAny]

    def get(self, request, course_id):
        path = request.build_absolute_uri(f"/api/courses/{course_id}/content/")
        return Response({"message": f"Use {path} instead for flat structure"}, status=410)


# ════════════════════════════════════════════════════════════
# SECTION TYPES
# ════════════════════════════════════════════════════════════

class SectionTypeListView(APIView):
    """
    GET /section-types/
    Returns the available section types for frontend dropdowns.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        from .models import Module
        
        # Build list from Django choices defined on the Module model
        data = [
            {"id": choice[0], "label": choice[1]}
            for choice in Module.SECTION_TYPES
        ]
        return Response(data)


# ════════════════════════════════════════════════════════════
# ASSIGNMENTS
# ════════════════════════════════════════════════════════════

class AssignmentCreateUpdateView(APIView):
    """
    GET  /modules/<module_id>/lessons/<lesson_id>/assignment/
    List all assignments for a lesson.
    
    POST /modules/<module_id>/lessons/<lesson_id>/assignment/
    Allows an instructor or admin to create a new assignment on a lesson (max 5).
    """
    def get_permissions(self):
        if self.request.method in ["POST", "PUT", "PATCH", "DELETE"]:
            return [CanEditCourseContent()]
        return [AllowAny()]
    
    def get(self, request, module_id, lesson_id):
        try:
            lesson = Lesson.objects.get(id=lesson_id, module_id=module_id)
        except Lesson.DoesNotExist:
            return Response({"error": "Lesson not found in this module"}, status=404)

        assignments = lesson.assignments.all()
        serializer = AssignmentSerializer(assignments, many=True)
        return Response(serializer.data)

    def delete(self, request, module_id, lesson_id):
        try:
            lesson = Lesson.objects.get(id=lesson_id, module_id=module_id)
        except Lesson.DoesNotExist:
            return Response({"error": "Lesson not found in this module"}, status=404)

        permission = CanEditCourseContent()
        if not permission.has_object_permission(request, self, lesson):
            return Response({"error": "You don't have permission to delete assignments for this lesson"}, status=403)
        
        # Determine if a specific assignment_id was sent in query params
        assignment_id = request.query_params.get('assignment_id')
        if assignment_id:
            try:
                assignment = lesson.assignments.get(id=assignment_id)
                assignment.delete()
                return Response({"message": "Assignment deleted successfully"}, status=204)
            except Assignment.DoesNotExist:
                return Response({"error": "Assignment not found"}, status=404)
        else:
            # Delete all assignments for this lesson
            lesson.assignments.all().delete()
            return Response({"message": "All assignments for this lesson deleted"}, status=204)

    def post(self, request, module_id, lesson_id):
        try:
            lesson = Lesson.objects.get(id=lesson_id, module_id=module_id)
        except Lesson.DoesNotExist:
            return Response({"error": "Lesson not found in this module"}, status=404)

        permission = CanEditCourseContent()
        if not permission.has_object_permission(request, self, lesson):
            return Response({"error": "You don't have permission to create assignments for this lesson"}, status=403)
        
        if lesson.assignments.count() >= 5:
            return Response({"error": "You can only create up to 5 assignments per lesson"}, status=400)
            
        data = request.data.copy()
        data["lesson"] = lesson.id

        serializer = AssignmentSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            assignment = serializer.save()
            
            # 🔥 Send Notification Email to students in the batch
            if assignment.batch:
                try:
                    student_emails = list(assignment.batch.students.values_list('email', flat=True))
                    if student_emails:
                        from django.core.mail import send_mail
                        from django.conf import settings
                        
                        due_date_str = assignment.assignment_due_date.strftime('%Y-%m-%d %H:%M') if assignment.assignment_due_date else "No due date"
                        subject = f"New Assignment: {assignment.assignment_title}"
                        
                        message = f"Hello,\n\nA new assignment '{assignment.assignment_title}' has been posted for your batch '{assignment.batch.name}'.\n\n"
                        message += f"Description: {assignment.assignment_description}\n"
                        message += f"Due Date: {due_date_str}\n\n"
                        message += "Please login to your dashboard to view and submit your work.\n\nBest regards,\nNexGen Team"
                        
                        send_mail_count = 0
                        for email in student_emails:
                            try:
                                send_mail(
                                    subject,
                                    message,
                                    settings.EMAIL_HOST_USER,
                                    [email],
                                    fail_silently=False,
                                )
                                send_mail_count += 1
                            except Exception as email_err:
                                print(f"Failed to send email to {email}: {email_err}")
                        
                        print(f"Successfully sent {send_mail_count} assignment notification emails.")
                except Exception as e:
                    print(f"Error processing assignment emails: {e}")

            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)
        try:
            lesson = Lesson.objects.get(id=lesson_id, module_id=module_id)
        except Lesson.DoesNotExist:
            return Response({"error": "Lesson not found in this module"}, status=404)

        permission = CanEditCourseContent()
        if not permission.has_object_permission(request, self, lesson):
            return Response({"error": "You don't have permission to create assignments for this lesson"}, status=403)
        
        if lesson.assignments.count() >= 5:
            return Response({"error": "You can only create up to 5 assignments per lesson"}, status=400)
            
        data = request.data.copy()
        data["lesson"] = lesson.id

        serializer = AssignmentSerializer(data=data)
        if serializer.is_valid():
            assignment = serializer.save()
            
            # 🔥 Send Notification Email to students in the batch
            if assignment.batch:
                try:
                    student_emails = list(assignment.batch.students.values_list('email', flat=True))
                    if student_emails:
                        from django.core.mail import send_mail
                        from django.conf import settings
                        
                        due_date_str = assignment.assignment_due_date.strftime('%Y-%m-%d %H:%M') if assignment.assignment_due_date else "No due date"
                        subject = f"New Assignment: {assignment.assignment_title}"
                        
                        message = f"Hello,\n\nA new assignment '{assignment.assignment_title}' has been posted for your batch '{assignment.batch.name}'.\n\n"
                        message += f"Description: {assignment.assignment_description}\n"
                        message += f"Due Date: {due_date_str}\n\n"
                        message += "Please login to your dashboard to view and submit your work.\n\nBest regards,\nNexGen Team"
                        
                        send_mail_count = 0
                        for email in student_emails:
                            try:
                                send_mail(
                                    subject,
                                    message,
                                    settings.EMAIL_HOST_USER,
                                    [email],
                                    fail_silently=False,
                                )
                                send_mail_count += 1
                            except Exception as email_err:
                                print(f"Failed to send email to {email}: {email_err}")
                        
                        print(f"Successfully sent {send_mail_count} assignment notification emails.")
                except Exception as e:
                    print(f"Error processing assignment emails: {e}")

            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

class AssignmentListCreateView(APIView):
    """
    GET /api/assignments/
    POST /api/assignments/
    """
    permission_classes = [IsAdminOrInstructor]

    def get(self, request):
        from accounts.models import User
        user = request.user
        
        # 🔍 Extraction of Query Parameters
        search_query = request.query_params.get('search', '')
        instructor_id = request.query_params.get('instructor_id')
        batch_id = request.query_params.get('batch_id')
        course_id = request.query_params.get('course_id')

        # 🔐 Base Queryset based on role
        if user.is_superuser or getattr(user, 'role', '') == User.ADMIN:
            assignments = Assignment.objects.all()
        else:
            instructor = getattr(user, 'instructor', None)
            if not instructor:
                return Response([], status=200)
            assignments = Assignment.objects.filter(instructor=instructor)
            
        # 🛠️ Apply Filters
        if search_query:
            assignments = assignments.filter(assignment_title__icontains=search_query)
        
        if course_id:
            assignments = assignments.filter(lesson__module__course_id=course_id)
            
        if instructor_id:
            assignments = assignments.filter(instructor_id=instructor_id)
            
        if batch_id:
            assignments = assignments.filter(batch_id=batch_id)
            
        assignments = assignments.select_related('lesson__module__course', 'batch', 'instructor')
        serializer = AssignmentSerializer(assignments, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = AssignmentSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            assignment = serializer.save()
            
            # 🔥 Send Notification Email to students in the batch (logic copied from lesson-based view)
            if assignment.batch:
                try:
                    student_emails = list(assignment.batch.students.values_list('email', flat=True))
                    if student_emails:
                        from django.core.mail import send_mail
                        from django.conf import settings
                        
                        due_date_str = assignment.assignment_due_date.strftime('%Y-%m-%d %H:%M') if assignment.assignment_due_date else "No due date"
                        subject = f"New Assignment: {assignment.assignment_title}"
                        
                        message = f"Hello,\n\nA new assignment '{assignment.assignment_title}' has been posted for your batch '{assignment.batch.name}'.\n\n"
                        message += f"Description: {assignment.assignment_description}\n"
                        message += f"Due Date: {due_date_str}\n\n"
                        message += "Please login to your dashboard to view and submit your work.\n\nBest regards,\nNexGen Team"
                        
                        for email in student_emails:
                            try:     
                                send_mail(subject, message, settings.EMAIL_HOST_USER, [email], fail_silently=False)
                            except: pass
                except: pass

            return Response(AssignmentSerializer(assignment).data, status=201)
        return Response(serializer.errors, status=400)

    def delete(self, request, module_id, lesson_id):
        """
        Deletes an assignment.
        - Use ?delete_all=true to delete ALL assignments for this lesson.
        - Use ?assignment_id=<id> to delete a specific one.
        - If only one exists, it deletes it automatically without parameters.
        """
        assignment_id = request.query_params.get('assignment_id')
        delete_all = request.query_params.get('delete_all')
        assignments = Assignment.objects.filter(lesson_id=lesson_id)

        # 🚀 Option to delete everything to fix stale data issues quickly
        if delete_all == 'true':
            count = assignments.count()
            for a in assignments:
                a.submissions.all().delete()
            assignments.delete()
            return Response({"message": f"All {count} assignments and their submissions deleted successfully"}, status=204)

        if not assignment_id:
            if assignments.count() == 1:
                assignment = assignments.first()
            elif assignments.count() > 1:
                return Response({
                    "error": "Multiple assignments found for this lesson. Please specify assignment_id OR use delete_all=true.",
                    "available_assignments": [{"id": a.id, "title": a.assignment_title} for a in assignments]
                }, status=400)
            else:
                return Response({"error": "No assignment found for this lesson"}, status=404)
        else:
            try:
                assignment = assignments.get(id=assignment_id)
            except Assignment.DoesNotExist:
                return Response({"error": f"Assignment with ID {assignment_id} not found for this lesson"}, status=404)

        # Cleanup: delete submissions first
        assignment.submissions.all().delete()
        assignment.delete()
        
        return Response({"message": "Assignment deleted successfully"}, status=204)


class AssignmentSubmitView(APIView):
    """
    POST /assignments/<assignment_id>/submit/
    Student submits their assignment answer.
    """
    permission_classes = [AllowAny] # Checked manually for enrollment below
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, assignment_id=None, module_id=None, lesson_id=None):
        from .models import Assignment, Submission
        from enrollments.models import Enrollment

        if not request.user.is_authenticated or request.user.role != 'student':
            return Response({"error": "Only enrolled students can submit assignments."}, status=403)

        # Handle 'undefined' from frontend
        if module_id == 'undefined': module_id = None
        if lesson_id == 'undefined': lesson_id = None
        if assignment_id == 'undefined': assignment_id = None

        try:
            if assignment_id:
                assignment = Assignment.objects.get(id=assignment_id)
            else:
                # Try finding by lesson_id
                assignment = Assignment.objects.filter(lesson_id=lesson_id).first()
                if not assignment and lesson_id:
                    # HEURISTIC: If lesson_id 11 is passed but is actually an assignment ID
                    assignment = Assignment.objects.filter(id=lesson_id).first()
                
                if not assignment:
                    return Response({"error": "No assignment found for lesson/id provided"}, status=404)
        except (Assignment.DoesNotExist, ValueError):
            return Response({"error": "Assignment not found"}, status=404)

        # Check enrollment via course
        course = assignment.lesson.module.course
        if not Enrollment.objects.filter(email=request.user.email, course=course, status='approved').exists():
            return Response({"error": "You are not enrolled in this course."}, status=403)

        # If this lesson is instructor-specific, only students from that instructor's active batches can submit.
        lesson_instructor = assignment.lesson.module.created_by
        if lesson_instructor:
            is_student_in_batch = Batch.objects.filter(
                course=course,
                instructor=lesson_instructor,
                is_active=True,
                students=request.user,
            ).exists()
            if not is_student_in_batch:
                return Response({"error": "This assignment is not assigned to your batch."}, status=403)

        # Check if already submitted
        submission = Submission.objects.filter(assignment=assignment, student=request.user).first()
        
        # Only allow answer payload from students.
        data = {
            'assignment': assignment.id,
            'student': request.user.id,
            'status': 'submitted',
            'text_answer': request.data.get('text_answer', ''),
            'file_upload': request.data.get('file_upload'),
            # Re-submission invalidates prior grading.
            'score': None,
            'feedback': '',
            'graded_at': None,
            'graded_by': None,
        }

        if submission:
            # Update existing submission
            serializer = SubmissionSerializer(submission, data=data, partial=True)
        else:
            # Create new submission
            serializer = SubmissionSerializer(data=data)

        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)


class AssignmentStatusView(APIView):
    """
    GET /assignments/<assignment_id>/status/
    Returns a unified list of enrolled students along with their submission status for a specific assignment.
    """
    def get_permissions(self):
        return [IsAssignedInstructorOrAdmin()]

    def get(self, request, assignment_id=None, module_id=None, lesson_id=None):
        from .models import Assignment, Submission
        from enrollments.models import Enrollment
        from django.contrib.auth import get_user_model

        # Handle 'undefined' from frontend
        if module_id == 'undefined': module_id = None
        if lesson_id == 'undefined': lesson_id = None
        if assignment_id == 'undefined': assignment_id = None

        User = get_user_model()

        try:
            if assignment_id:
                assignment = Assignment.objects.get(id=assignment_id)
            else:
                # Try finding by lesson_id (Get the LATEST one to avoid stale data from old assignments)
                assignment = Assignment.objects.filter(lesson_id=lesson_id).order_by('-created_at').first()
                if not assignment and lesson_id:
                    # HEURISTIC: If lesson_id 11 is passed but is actually an assignment ID
                    assignment = Assignment.objects.filter(id=lesson_id).first()
                
                if not assignment:
                    return Response({"error": "No assignment found for lesson/id provided"}, status=404)
        except (Assignment.DoesNotExist, ValueError):
            return Response({"error": "Assignment not found"}, status=404)

        course = assignment.lesson.module.course
        
        # Verify permissions 
        permission = IsAssignedInstructorOrAdmin()
        if not permission.has_object_permission(request, self, course):
            return Response({"error": "You don't have permission to view status for this assignment"}, status=403)

        # Build student scope.
        if request.user.is_superuser:
            approved_emails = Enrollment.objects.filter(course=course, status='approved').values_list('email', flat=True)
            enrolled_users = User.objects.filter(email__in=approved_emails).prefetch_related('enrolled_batches')
        elif hasattr(request.user, 'instructor'):
            instructor_batches = Batch.objects.filter(
                course=course,
                instructor=request.user.instructor,
                is_active=True,
            )
            enrolled_users = User.objects.filter(
                id__in=instructor_batches.values_list('students__id', flat=True)
            ).distinct().prefetch_related('enrolled_batches')
        else:
            enrolled_users = User.objects.none()

        # Get all submissions for this specific assignment
        submissions = Submission.objects.filter(assignment=assignment)
        submission_map = {sub.student_id: sub for sub in submissions}

        # Build response
        response_data = []
        for user in enrolled_users:
            sub = submission_map.get(user.id)
            
            if request.user.is_superuser:
                batch = user.enrolled_batches.filter(course=course, is_active=True).first()
            elif hasattr(request.user, 'instructor'):
                batch = user.enrolled_batches.filter(
                    course=course,
                    is_active=True,
                    instructor=request.user.instructor,
                ).first()
            else:
                batch = None
            batch_data = {"id": batch.id, "name": batch.name.name if getattr(batch, 'name', None) else f"Batch {batch.id}"} if batch else None

            if sub:
                response_data.append({
                    "submission_id": sub.id,
                    "student_id": user.id,
                    "student_name": f"{user.first_name} {user.last_name}".strip() or user.email,
                    "student_email": user.email,
                    "batch": batch_data,
                    "status": "Submitted",
                    "submitted_at": sub.submitted_at,
                    "score": sub.score,
                    "feedback": sub.feedback,
                    "graded_at": sub.graded_at,
                    "graded_by": f"{sub.graded_by.first_name} {sub.graded_by.last_name}".strip() or sub.graded_by.email if sub.graded_by else None,
                    "submission_data": SubmissionSerializer(sub).data
                })
            else:
                response_data.append({
                    "submission_id": None,
                    "student_id": user.id,
                    "student_name": f"{user.first_name} {user.last_name}".strip() or user.email,
                    "student_email": user.email,
                    "batch": batch_data,
                    "status": "Not Submitted",
                    "submitted_at": None,
                    "score": None,
                    "feedback": "",
                    "graded_at": None,
                    "graded_by": None,
                    "submission_data": None
                })

        return Response(response_data, status=200)


class AssignmentGradeView(APIView):
    """
    PATCH /modules/<module_id>/lessons/<lesson_id>/assignment/submissions/<submission_id>/grade/
    Allows assigned instructor/admin to grade an existing submission.
    """

    def get_permissions(self):
        return [IsAssignedInstructorOrAdmin()]

    def patch(self, request, module_id, lesson_id, submission_id):
        try:
            lesson = Lesson.objects.get(id=lesson_id, module_id=module_id)
        except Lesson.DoesNotExist:
            return Response({"error": "Lesson not found in this module"}, status=404)

        course = lesson.module.course
        permission = IsAssignedInstructorOrAdmin()
        if not permission.has_object_permission(request, self, course):
            return Response({"error": "You don't have permission to grade this assignment"}, status=403)

        try:
            # Filter by lesson indirectly via assignment
            submission = Submission.objects.get(id=submission_id, assignment__lesson=lesson)
        except Submission.DoesNotExist:
            return Response({"error": "Submission not found for this assignment"}, status=404)

        score_raw = request.data.get("score")
        feedback = (request.data.get("feedback") or "").strip()

        if score_raw in (None, ""):
            return Response({"error": "score is required"}, status=400)

        try:
            score = int(score_raw)
        except (TypeError, ValueError):
            return Response({"error": "score must be an integer"}, status=400)

        if score < 0 or score > 100:
            return Response({"error": "score must be between 0 and 100"}, status=400)

        submission.score = score
        submission.feedback = feedback
        submission.status = "graded"
        submission.graded_at = timezone.now()
        submission.graded_by = request.user
        submission.save(update_fields=["score", "feedback", "status", "graded_at", "graded_by"])

        return Response(SubmissionSerializer(submission).data, status=200)

class InstructorStudentDetailView(APIView):
    """
    GET /student-assignments/<student_id>/
    Returns list of assignments for a specific student across their enrolled courses, 
    with submission status. Useful for instructor detail view.
    """
    def get_permissions(self):
        from accounts.permissions import IsInstructor
        return [IsInstructor()]

    def get(self, request, student_id):
        from django.contrib.auth import get_user_model
        from enrollments.models import Enrollment
        from .models import Assignment, Submission
        
        User = get_user_model()
        try:
            student = User.objects.get(id=student_id)
        except User.DoesNotExist:
            return Response({"error": "Student not found"}, status=404)

        # Get courses this student is enrolled in that are also assigned to this instructor (or all if admin)
        enrollment_qs = Enrollment.objects.filter(email=student.email, status='approved')
        
        if not request.user.is_superuser and hasattr(request.user, 'instructor'):
            assigned_courses = request.user.instructor.assigned_courses.all()
            enrollment_qs = enrollment_qs.filter(course__in=assigned_courses)
        elif not request.user.is_superuser:
             return Response({"error": "Instructor profile not found."}, status=403)

        enrolled_course_ids = enrollment_qs.values_list('course_id', flat=True)

        assignments = Assignment.objects.filter(
            lesson__module__course__id__in=enrolled_course_ids
        ).select_related('lesson__module__course')

        data = []
        for assignment in assignments:
            submission = Submission.objects.filter(
                assignment=assignment, student=student
            ).first()
            data.append({
                "assignment": AssignmentSerializer(assignment).data,
                "course": {
                    "id": assignment.lesson.module.course.id,
                    "title": assignment.lesson.module.course.title,
                },
                "status": submission.status if submission else "Not Submitted",
                "submitted_at": submission.submitted_at if submission else None,
                "submission_data": SubmissionSerializer(submission).data if submission else None
            })

        return Response(data)


class AssignmentDetailView(APIView):
    """
    GET    /assignments/<pk>/  → retrieve assignment details
    PUT    /assignments/<pk>/  → update assignment (instructor/admin)
    DELETE /assignments/<pk>/  → delete assignment (instructor/admin)
    """
    def get_permissions(self):
        return [IsAdminOrInstructor()]

    def get_object(self, pk):
        try:
            return Assignment.objects.get(pk=pk)
        except Assignment.DoesNotExist:
            return None

    def get(self, request, pk):
        assignment = self.get_object(pk)
        if not assignment:
            return Response({"error": "Assignment not found"}, status=404)
        serializer = AssignmentSerializer(assignment)
        return Response(serializer.data)

    def put(self, request, pk):
        assignment = self.get_object(pk)
        if not assignment:
            return Response({"error": "Assignment not found"}, status=404)

        permission = IsAdminOrInstructor()
        if not permission.has_object_permission(request, self, assignment):
            return Response({"error": "You don't have permission to edit this assignment"}, status=403)

        serializer = AssignmentSerializer(assignment, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def patch(self, request, pk):
        return self.put(request, pk)

    def delete(self, request, pk):
        assignment = self.get_object(pk)
        if not assignment:
            return Response({"error": "Assignment not found"}, status=404)

        permission = IsAdminOrInstructor()
        if not permission.has_object_permission(request, self, assignment):
            return Response({"error": "You don't have permission to delete this assignment"}, status=403)

        # 🔥 CRITICAL: Remove all student submissions for this specific assignment when deleted.
        assignment.submissions.all().delete()
        assignment.delete()

        return Response({"message": "Assignment deleted successfully"}, status=204)


class StudentAssignmentListView(APIView):
    """
    GET /my-assignments/
    Returns all lessons marked as assignments for a student's enrolled courses,
    along with their submission status.
    """
    def get_permissions(self):
        return [IsStudent()]

    def get(self, request):
        from enrollments.models import Enrollment
        from .models import Assignment, Submission

        enrolled_course_ids = Enrollment.objects.filter(
            email=request.user.email,
            status='approved'
        ).values_list('course_id', flat=True)

        assignments = Assignment.objects.filter(
            lesson__module__course__id__in=enrolled_course_ids
        ).select_related('lesson__module__course')

        data = []
        for assignment in assignments:
            submission = Submission.objects.filter(
                assignment=assignment, student=request.user
            ).first()
            data.append({
                "assignment": AssignmentSerializer(assignment).data,
                "course": {
                    "id": assignment.lesson.module.course.id,
                    "title": assignment.lesson.module.course.title,
                },
                "status": submission.status if submission else "Not Submitted",
                "submitted_at": submission.submitted_at if submission else None,
            })

        return Response(data)


class InstructorAssignmentListView(APIView):
    """
    GET /instructor-assignments/
    Returns all lessons marked as assignments across courses the instructor is assigned to.
    """
    def get_permissions(self):
        return [IsAssignedInstructorOrAdmin()]

    def get(self, request):
        from .models import Assignment
        from accounts.models import User
        
        instructor_id = request.query_params.get('instructor_id')
        course_id = request.query_params.get('course_id')
        batch_id = request.query_params.get('batch_id')

        # Base queryset
        if request.user.is_superuser or getattr(request.user, 'role', '') == User.ADMIN:
            assignments = Assignment.objects.all()
            if instructor_id:
                assignments = assignments.filter(instructor_id=instructor_id)
        else:
            instructor = getattr(request.user, 'instructor', None)
            if instructor:
                assignments = Assignment.objects.filter(instructor=instructor)
            else:
                assignments = Assignment.objects.none()

        # Apply cascading filters
        if course_id:
            assignments = assignments.filter(lesson__module__course_id=course_id)
        if batch_id:
            assignments = assignments.filter(batch_id=batch_id)

        assignments = assignments.select_related('lesson__module__course').order_by('-assignment_due_date')

        data = []
        for assignment in assignments:
            submission_count = Submission.objects.filter(assignment=assignment).count()
            data.append({
                "assignment_id": assignment.id,
                "assignment": AssignmentSerializer(assignment).data,
                "course": {
                    "id": assignment.lesson.module.course.id,
                    "title": assignment.lesson.module.course.title,
                },
                "module": {
                    "id": assignment.lesson.module.id,
                    "title": assignment.lesson.module.title,
                },
                "lesson": {
                    "id": assignment.lesson.id,
                    "title": assignment.lesson.title,
                },
                "submissions_count": submission_count,
            })

        return Response(data)


# ════════════════════════════════════════════════════════════
# BATCHES
# ════════════════════════════════════════════════════════════

class BatchListCreateView(APIView):
    def get_permissions(self):
        # IsSuperAdmin is already imported from courses.permissions at top of this file
        if self.request.method == "POST":
            return [IsSuperAdmin()]
        # GET is open to authenticated users; filtering happens in get()
        from rest_framework.permissions import IsAuthenticated
        return [IsAuthenticated()]

    def get(self, request):
        batches = Batch.objects.filter(is_active=True).order_by('-created_at')
        instructor_id = request.query_params.get('instructor_id')
        course_id = request.query_params.get('course_id')
        batch_name = request.query_params.get('batch_name')
        course_name = request.query_params.get('course_name')
        
        if request.user.is_authenticated:
            if getattr(request.user, 'role', '') == 'instructor' and not request.user.is_superuser:
                if hasattr(request.user, 'instructor'):
                    batches = batches.filter(instructor=request.user.instructor)
                else:
                    batches = batches.none()
            elif request.user.is_superuser or getattr(request.user, 'role', '') == 'admin':
                if instructor_id:
                    batches = batches.filter(instructor_id=instructor_id)
            
            # 🔥 NEW: Filter by course_id for cascading dropdown
            if course_id:
                batches = batches.filter(course_id=course_id)
            
            # 🔥 NEW: Filter by Campaign Name (Batch Name)
            if batch_name:
                batches = batches.filter(name__name__icontains=batch_name)
            
            # 🔥 NEW: Filter by Course Title
            if course_name:
                batches = batches.filter(course__title__icontains=course_name)
        else:
            batches = batches.none() # Return nothing for unauthenticated

        serializer = BatchSerializer(batches, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = BatchSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class BatchDetailView(APIView):
    def get_permissions(self):
        return [IsSuperAdmin()]

    def get(self, request, pk):
        try:
            batch = Batch.objects.get(pk=pk)
            serializer = BatchSerializer(batch)
            return Response(serializer.data)
        except Batch.DoesNotExist:
            return Response({"error": "Batch not found"}, status=404)

    def put(self, request, pk):
        try:
            batch = Batch.objects.get(pk=pk)
        except Batch.DoesNotExist:
            return Response({"error": "Batch not found"}, status=404)

        serializer = BatchSerializer(batch, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def patch(self, request, pk):
        return self.put(request, pk)

    def delete(self, request, pk):
        try:
            batch = Batch.objects.get(pk=pk)
            batch.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Batch.DoesNotExist:
            return Response({"error": "Batch not found"}, status=404)

class ManageBatchStudentsView(APIView):
    def get_permissions(self):
        return [IsSuperAdmin()]

    def post(self, request, pk):
        try:
            batch = Batch.objects.get(pk=pk)
        except Batch.DoesNotExist:
            return Response({"error": "Batch not found"}, status=404)
        
        student_emails = request.data.get('student_emails', [])
        action = request.data.get('action', 'add')

        from django.contrib.auth import get_user_model
        User = get_user_model()
        students = User.objects.filter(email__in=student_emails)

        if action == 'add':
            batch.students.add(*students)
        elif action == 'remove':
            batch.students.remove(*students)
            
        return Response({"message": "Batch students updated successfully."})

class InstructorBatchListView(APIView):
    def get_permissions(self):
        from accounts.permissions import IsInstructor
        return [IsInstructor()]

    def get(self, request):
        if hasattr(request.user, 'instructor'):
            # Directly filter Batch objects by instructor to ensure fresh data
            batches = Batch.objects.filter(
                instructor=request.user.instructor, 
                is_active=True
            ).prefetch_related('students', 'course')
            
            data = []
            for batch in batches:
                data.append({
                    "id": batch.id,
                    "name": batch.name.name if batch.name else None,
                    "course_id": batch.course.id,
                    "course_title": batch.course.title,
                    "live_link": batch.live_link,
                    "is_live_class_active": batch.is_live_class_active,
                    "students": [
                        {
                            "id": student.id,
                            "name": f"{student.first_name} {student.last_name}".strip() or student.email,
                            "email": student.email,
                            "phone": getattr(student, 'phone', None)
                        } for student in batch.students.all()
                    ]
                })
            return Response(data)
        return Response({"error": "Instructor profile not found."}, status=403)


class ManageLiveClassView(APIView):
    def get_permissions(self):
        from rest_framework.permissions import IsAuthenticated
        return [IsAuthenticated()]

    def get(self, request, pk):
        try:
            batch = Batch.objects.get(pk=pk)
        except Batch.DoesNotExist:
            return Response({'error': 'Batch not found'}, status=404)

        # Optionally check permissions for viewing (Student in batch, Assigned Instructor, Admin)
        if getattr(request.user, 'role', '') == 'student':
            if not batch.students.filter(id=request.user.id).exists():
                return Response({'error': 'Not enrolled in this batch'}, status=403)
        elif getattr(request.user, 'role', '') == 'instructor':
            if not hasattr(request.user, 'instructor') or batch.instructor != request.user.instructor:
                return Response({'error': 'Not assigned to this batch'}, status=403)

        return Response({
            'id': batch.id,
            'name': batch.name.name if batch.name else None,
            'is_live_class_active': batch.is_live_class_active,
            'live_link': batch.live_link if batch.is_live_class_active else None
        })

    def post(self, request, pk):
        try:
            batch = Batch.objects.get(pk=pk)
        except Batch.DoesNotExist:
            return Response({'error': 'Batch not found'}, status=404)

        if request.user.role == 'instructor':
            if not hasattr(request.user, 'instructor') or batch.instructor != request.user.instructor:
                return Response({'error': 'Not assigned to this batch'}, status=403)
        elif request.user.role != 'admin' and not request.user.is_superuser:
            return Response({'error': 'Permission denied'}, status=403)

        action = request.data.get('action')
        if action == 'start':
            link = request.data.get('live_link')
            if not link:
                return Response({'error': 'Meeting link is required'}, status=400)
            batch.live_link = link
            batch.is_live_class_active = True
            batch.save()
            return Response({'message': 'Live class started', 'live_link': batch.live_link})
        elif action == 'end':
            batch.is_live_class_active = False
            batch.live_link = ''
            batch.save()
            return Response({'message': 'Live class ended'})

        return Response({'error': 'Invalid action'}, status=400)

    def put(self, request, pk):
        try:
            batch = Batch.objects.get(pk=pk)
        except Batch.DoesNotExist:
            return Response({'error': 'Batch not found'}, status=404)

        if request.user.role == 'instructor':
            if not hasattr(request.user, 'instructor') or batch.instructor != request.user.instructor:
                return Response({'error': 'Not assigned to this batch'}, status=403)
        elif request.user.role != 'admin' and not request.user.is_superuser:
            return Response({'error': 'Permission denied'}, status=403)

        is_active = request.data.get('is_live_class_active')
        live_link = request.data.get('live_link')

        if is_active is not None:
            batch.is_live_class_active = str(is_active).lower() in ['true', '1', 't', 'y', 'yes']
        if live_link is not None:
            batch.live_link = live_link
        
        batch.save()
        return Response({
            'message': 'Live class updated successfully',
            'is_live_class_active': batch.is_live_class_active,
            'live_link': batch.live_link
        })


from .storage import get_signed_url

class FileAccessView(APIView):
    """
    GET /api/courses/files/access/?type=<type>&id=<id>
    Generates a secure, signed URL for private files.
    Types: lesson, assignment, submission
    """
    def get(self, request):
        file_type = request.query_params.get('type')
        obj_id = request.query_params.get('id')

        if obj_id == 'undefined': obj_id = None

        if not file_type or not obj_id:
            return Response({"error": "Missing type or id"}, status=400)

        file_field = None
        try:
            if file_type == 'lesson':
                try:
                    obj = Lesson.objects.get(id=obj_id)
                    # Permission check
                    permission = CanEditCourseContent()
                    if not permission.has_object_permission(request, self, obj) and request.user.role == 'student':
                        from enrollments.models import Enrollment
                        if not Enrollment.objects.filter(email=request.user.email, course=obj.module.course, status='approved').exists():
                            return Response({"error": "No permission to access this lesson file"}, status=403)
                    file_field = obj.file
                except (Lesson.DoesNotExist, ValueError):
                    # Check if it's actually an assignment ID
                    obj = Assignment.objects.filter(id=obj_id).first()
                    if obj:
                        permission = CanEditCourseContent()
                        if not permission.has_object_permission(request, self, obj.lesson) and request.user.role == 'student':
                            from enrollments.models import Enrollment
                            if not Enrollment.objects.filter(email=request.user.email, course=obj.lesson.module.course, status='approved').exists():
                                return Response({"error": "No permission to access this assignment file"}, status=403)
                        file_field = obj.file
                    else:
                        return Response({"error": f"Lesson with ID {obj_id} not found"}, status=404)
                
            elif file_type == 'assignment':
                try:
                    obj = Assignment.objects.get(id=obj_id)
                except (Assignment.DoesNotExist, ValueError):
                    # Frontend sometimes sends lesson_id as id when requesting assignment files
                    obj = Assignment.objects.filter(lesson_id=obj_id).order_by('-created_at').first()
                    if not obj:
                        raise Assignment.DoesNotExist

                permission = CanEditCourseContent()
                if not permission.has_object_permission(request, self, obj) and request.user.role == 'student':
                    from enrollments.models import Enrollment
                    if not Enrollment.objects.filter(email=request.user.email, course=obj.lesson.module.course, status='approved').exists():
                        return Response({"error": "No permission to access this assignment file"}, status=403)
                file_field = obj.file

            elif file_type == 'submission':
                obj = Submission.objects.get(id=obj_id)
                # Permission check: student must be owner, or instructor/admin
                if obj.student != request.user:
                    permission = IsAssignedInstructorOrAdmin()
                    if not permission.has_object_permission(request, self, obj.assignment.lesson.module.course):
                        return Response({"error": "No permission to access this submission file"}, status=403)
                file_field = obj.file_upload
            else:
                return Response({"error": "Invalid type"}, status=400)
        except (Lesson.DoesNotExist, Assignment.DoesNotExist, Submission.DoesNotExist, ValueError):
            return Response({"error": f"{file_type.capitalize()} with ID {obj_id} not found"}, status=404)

        if not file_field:
            return Response({"error": "No file attached"}, status=404)

        # Generate signed URL
        try:
            public_id = file_field.name
            signed_url = get_signed_url(public_id)
            return Response({"signed_url": signed_url})
        except Exception as e:
            return Response({"error": str(e)}, status=500)

