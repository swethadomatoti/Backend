from django.db import models
from courses.models import Course


class Enrollment(models.Model):

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    COURSE_TYPE_CHOICES = (
        ('Training', 'training'),
        ('Industry Readiness', 'industry readiness'),
    )

    MODE_CHOICES = (
        ('Online', 'online'),
        ('Offline', 'offline'),
    )

    TIMING_CHOICES = (
        ('Morning', 'morning'),
        ('Afternoon', 'afternoon'),
        ('Evening', 'evening'),
    )

    EXPERIENCE_CHOICES = (
        ('Beginner', 'beginner'),
        ('Intermediate', 'intermediate'),
        ('Experienced', 'experienced'),
    )

    CURRENT_STATUS_CHOICES = (
        ('Student', 'student'),
        ('Working', 'working'),
        ('Job Seeker', 'job_seeker'),
    )


    # 🔥 BASIC INFO
    name = models.CharField(max_length=200)
    email = models.EmailField()
    phone = models.CharField(max_length=15)

    course = models.ForeignKey(Course, on_delete=models.CASCADE)

    # 🔥 NEW FIELDS FROM UI
    course_type = models.CharField(max_length=20, choices=COURSE_TYPE_CHOICES)
    qualification = models.CharField(max_length=100)
    current_status = models.CharField(max_length=50, choices=CURRENT_STATUS_CHOICES)

    organization = models.CharField(
        max_length=200,
        blank=True,
        null=True
    )  # college/company

    preferred_mode = models.CharField(max_length=20, choices=MODE_CHOICES)
    preferred_timing = models.CharField(max_length=50, choices=TIMING_CHOICES)

    experience_level = models.CharField(max_length=20, choices=EXPERIENCE_CHOICES)

    terms_accepted = models.BooleanField(default=False)

    # 🔥 PAYMENT
    payment_status = models.CharField(max_length=20, default="pending")
    razorpay_order_id = models.CharField(max_length=200, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=200, blank=True, null=True)

    # 🔥 STATUS
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')                                                             
    is_active = models.BooleanField(default=False)

    # 🔥 NEW FIELDS
    enrollment_date = models.DateField(null=True, blank=True)
    fee_status = models.CharField(
        max_length=20, 
        choices=[
            ('Pending', 'Pending'),
            ('Paid', 'Paid'),
            ('Partially Paid', 'Partially Paid'),
        ],
        default='Pending'
    )
    
    # Support for lead linking if needed
    lead = models.ForeignKey(
        'LeadManagement.Lead',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='enrolled_records'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('email', 'course')

    def __str__(self):
        return f"{self.name} - {self.course.title}"

    @property
    def full_name(self):
        return self.name

    @property
    def phone_number(self):
        return self.phone

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_status = self.status


# --- Signal to handle approval, user creation, and credentials email ---
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from accounts.models import StudentProfile
import random
import string
import re
import logging
from .tasks import send_student_approval_email_sync

logger = logging.getLogger(__name__)

class PaymentDetail(models.Model):
    enrollment = models.ForeignKey(
        Enrollment, 
        on_delete=models.CASCADE, 
        related_name="payment_details"
    )
    fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    payment_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    remaining_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # 1. If fee_amount is 0.0 or not set, get it from the Course price
        if not self.fee_amount and self.enrollment and self.enrollment.course:
            self.fee_amount = self.enrollment.course.price

        # 2. Automatically calculate the remaining balance
        if self.fee_amount is not None and self.payment_paid is not None:
            self.remaining_balance = float(self.fee_amount) - float(self.payment_paid)
            
        super().save(*args, **kwargs)

        # 3. Update the associated Enrollment's fee_status
        if self.enrollment:
            if float(self.payment_paid) <= 0:
                new_status = 'Pending'
            elif float(self.remaining_balance) <= 0:
                new_status = 'Paid'
            else:
                new_status = 'Partially Paid'
            
            if self.enrollment.fee_status != new_status:
                self.enrollment.fee_status = new_status
                # We use update_fields so we don't trigger unnecessary save logic loops in Enrollment
                self.enrollment.save(update_fields=['fee_status'])

    def __str__(self):
        return f"Payment for {self.enrollment.name} - {self.payment_paid}"


class PaymentTransaction(models.Model):
    payment_detail = models.ForeignKey(
        PaymentDetail,
        on_delete=models.CASCADE,
        related_name="transactions"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_at']

    def __str__(self):
        return f"{self.payment_detail.enrollment.name} paid {self.amount} on {self.paid_at}"


@receiver(post_save, sender=Enrollment)
def handle_enrollment_approval(sender, instance, created, **kwargs):
    if instance.status == 'approved' and (created or getattr(instance, '_original_status', None) != 'approved'):
        User = get_user_model()
        try:
            user = User.objects.filter(email=instance.email).first()
            temp_password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))

            base_username = re.sub(r'[^a-zA-Z0-9]+', '', (instance.name or '').lower()) or instance.email.split("@")[0]
            username = base_username
            suffix = 1
            while User.objects.filter(username=username).exclude(email=instance.email).exists():
                suffix += 1
                username = f"{base_username}{suffix}"

            if not user:
                user = User.objects.create_user(
                    username=username,
                    email=instance.email,
                    password=temp_password,
                    role="student"
                )
            else:
                user.role = "student"
                user.username = username
                user.set_password(temp_password)

            user.is_active = True
            user.save()

            student_profile, _ = StudentProfile.objects.get_or_create(user=user)
            student_profile.is_first_login = True
            student_profile.save(update_fields=["is_first_login"])

            # Ensure instance is active
            if not instance.is_active:
                instance.is_active = True
                instance.save(update_fields=['is_active'])

            try:
                send_student_approval_email_sync(
                    instance.name,
                    user.username,
                    temp_password,
                    instance.course.title,
                    instance.email
                )
                logger.info(f"Student approval email sent successfully to {instance.email}")
            except Exception as email_error:
                logger.error(f"Enrollment approved, but student email failed: {str(email_error)}")

        except Exception as e:
            logger.error(f"Failed to process enrollment approval for {instance.email}: {str(e)}")

        # Update original status so it doesn't fire again on subsequent saves in this instance
        instance._original_status = 'approved'