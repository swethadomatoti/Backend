from rest_framework import serializers
from .models import Enrollment, PaymentDetail, PaymentTransaction
from courses.models import Course


from LeadManagement.models import Lead

class PaymentTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentTransaction
        fields = ['id', 'amount', 'paid_at']
        read_only_fields = ['id', 'paid_at']


class PaymentDetailSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='enrollment.course.title', read_only=True)
    enrollment_name = serializers.CharField(source='enrollment.name', read_only=True)
    transactions = PaymentTransactionSerializer(many=True, read_only=True)
    class Meta:
        model = PaymentDetail
        fields = '__all__'
        read_only_fields = ['remaining_balance']


class EnrollmentSerializer(serializers.ModelSerializer):
    full_name = serializers.ReadOnlyField()
    phone_number = serializers.ReadOnlyField()
    course_title = serializers.ReadOnlyField(source='course.title')
    course = serializers.SlugRelatedField(
        queryset=Course.objects.all(),
        slug_field='title',
        required=False
    )
    
    # Alias for lead_id input
    lead_id = serializers.PrimaryKeyRelatedField(
        queryset=Lead.objects.all(),
        source='lead',
        required=False,
        write_only=True
    )
    
    # Read-only nested payment details
    payment_details = PaymentDetailSerializer(source='payment_detail', read_only=True)
    
    # Or to fetch from related_name='payment_details' (which returns a queryset) we can use a SerializerMethodField
    payment_detail = serializers.SerializerMethodField()

    # Flat convenience fields mirroring payment_detail, since most admin
    # frontend pages (Transactions/Reports/Refunds) read these top-level
    # rather than nested under payment_detail.
    fee_amount = serializers.SerializerMethodField()
    payment_paid = serializers.SerializerMethodField()
    remaining_balance = serializers.SerializerMethodField()

    # New field to show all courses the student is enrolled in
    all_enrolled_courses = serializers.SerializerMethodField()

    class Meta:
        model = Enrollment
        fields = "__all__"
        read_only_fields = ["status", "is_active"]

    def get_all_enrolled_courses(self, obj):
        if obj.email:
            enrollments = Enrollment.objects.filter(email=obj.email).select_related('course')
            return [
                {
                    "enrollment_id": e.id,
                    "course_id": e.course.id if e.course else None,
                    "course_title": e.course.title if e.course else None,
                    "status": e.status,
                    "fee_status": getattr(e, 'fee_status', 'Pending'),
                }
                for e in enrollments
            ]
        return []

    def get_payment_detail(self, obj):
        payment = obj.payment_details.first()
        if payment:
            return PaymentDetailSerializer(payment).data
        return None

    def _course_price(self, obj):
        return float(obj.course.price) if obj.course and hasattr(obj.course, 'price') else 0.0

    def get_fee_amount(self, obj):
        payment = obj.payment_details.first()
        return float(payment.fee_amount) if payment else self._course_price(obj)

    def get_payment_paid(self, obj):
        payment = obj.payment_details.first()
        return float(payment.payment_paid) if payment else 0.0

    def get_remaining_balance(self, obj):
        payment = obj.payment_details.first()
        return float(payment.remaining_balance) if payment else self._course_price(obj)

    def validate(self, data):
        # 1. Lead lookup by email if not provided
        email = data.get('email')
        lead = data.get('lead')
        
        if not lead and email:
            lead = Lead.objects.filter(email=email).first()
            if lead:
                data['lead'] = lead

        # 2. Existing validations
        if not data.get("terms_accepted"):
            raise serializers.ValidationError("You must accept terms & conditions")

        if Enrollment.objects.filter(
            email=data.get("email"),
            course=data.get("course")
        ).exclude(id=self.instance.id if self.instance else None).exists():
            raise serializers.ValidationError("Already enrolled for this course")

        return data

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        # If lead is null in DB, try to find it by email on the fly
        if ret.get('lead') is None and instance.email:
            lead = Lead.objects.filter(email=instance.email).first()
            if lead:
                ret['lead'] = lead.id
                # Fix it in the database so it's persistent for next time
                instance.lead = lead
                instance.save(update_fields=['lead'])
        
        # Replace course ID with course title
        if instance.course:
            ret['course'] = instance.course.title
            
        return ret

from courses.models import Lesson
from learning.models import LessonProgress

class StudentEnrolledCourseSerializer(serializers.ModelSerializer):
    course_title = serializers.CharField(source='course.title', read_only=True)
    instructor_name = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    completed_lessons = serializers.SerializerMethodField()
    total_lessons = serializers.SerializerMethodField()
    payment_detail = serializers.SerializerMethodField()

    class Meta:
        model = Enrollment
        fields = ['id', 'course_id', 'course_title', 'instructor_name', 'progress', 'completed_lessons', 'total_lessons', 'status', 'fee_status', 'payment_detail']

    def get_payment_detail(self, obj):
        payment = obj.payment_details.first()
        if payment:
            return {
                "fee_amount": float(payment.fee_amount),
                "payment_paid": float(payment.payment_paid),
                "remaining_balance": float(payment.remaining_balance)
            }
        else:
            return {
                "fee_amount": float(obj.course.price) if obj.course and hasattr(obj.course, 'price') else 0.0,
                "payment_paid": 0.0,
                "remaining_balance": float(obj.course.price) if obj.course and hasattr(obj.course, 'price') else 0.0
            }

    def get_instructor_name(self, obj):
        instructor = obj.course.instructor_assigned_courses.first()
        return instructor.full_name if instructor else 'Assigned soon'

    def get_total_lessons(self, obj):
        return Lesson.objects.filter(module__course=obj.course).count()

    def get_completed_lessons(self, obj):
        user = self.context.get('request').user
        return LessonProgress.objects.filter(student=user, lesson__module__course=obj.course, completed=True).count()

    def get_progress(self, obj):
        total = self.get_total_lessons(obj)
        if total == 0: return 0
        return round((self.get_completed_lessons(obj) / total) * 100)

